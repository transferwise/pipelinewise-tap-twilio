from datetime import timedelta
import math
import singer
from singer import metrics, metadata, Transformer, utils
from singer.utils import strptime_to_utc, strftime
from tap_twilio.transform import transform_json
from tap_twilio.streams import STREAMS

LOGGER = singer.get_logger()


def write_schema(catalog, stream_name):
    stream = catalog.get_stream(stream_name)
    schema = stream.schema.to_dict()
    try:
        singer.write_schema(stream_name, schema, stream.key_properties)
    except OSError as err:
        LOGGER.error('OS Error writing schema for: {}'.format(stream_name))
        raise err


def write_record(stream_name, record, time_extracted):
    try:
        singer.messages.write_record(stream_name, record, time_extracted=time_extracted)
    except OSError as err:
        LOGGER.error('OS Error writing record for: {}'.format(stream_name))
        LOGGER.error('Stream: {}, record: {}'.format(stream_name, record))
        raise err
    except TypeError as err:
        LOGGER.error('Type Error writing record for: {}'.format(stream_name))
        LOGGER.error('Stream: {}, record: {}'.format(stream_name, record))
        raise err


def get_bookmark(state, stream, default):
    if (state is None) or ('bookmarks' not in state):
        return default
    return (
        state
        .get('bookmarks', {})
        .get(stream, default)
    )


def write_bookmark(state, stream, value):
    if 'bookmarks' not in state:
        state['bookmarks'] = {}
    state['bookmarks'][stream] = value
    LOGGER.info('Write state for stream: {}, value: {}'.format(stream, value))
    singer.write_state(state)


def transform_datetime(this_dttm):
    with Transformer() as transformer:
        new_dttm = transformer._transform_datetime(this_dttm)
    return new_dttm


def process_records(catalog, #pylint: disable=too-many-branches
                    stream_name,
                    records,
                    time_extracted,
                    bookmark_field=None,
                    max_bookmark_value=None,
                    last_datetime=None,
                    parent=None,
                    parent_id=None):
    stream = catalog.get_stream(stream_name)
    schema = stream.schema.to_dict()
    stream_metadata = metadata.to_map(stream.metadata)

    with metrics.record_counter(stream_name) as counter:
        for record in records:
            # If child object, add parent_id to record
            if parent_id and parent:
                record[parent + '_id'] = parent_id

            # Transform record for Singer.io
            with Transformer() as transformer:
                try:
                    transformed_record = transformer.transform(
                        record,
                        schema,
                        stream_metadata)
                except Exception as err:
                    LOGGER.error('Transformer Error: {}'.format(err))
                    LOGGER.error('Stream: {}, record: {}'.format(stream_name, record))
                    raise err

                # Reset max_bookmark_value to new value if higher
                if transformed_record.get(bookmark_field):
                    if max_bookmark_value is None or \
                        transformed_record[bookmark_field] > transform_datetime(max_bookmark_value):
                        max_bookmark_value = transformed_record[bookmark_field]

                if bookmark_field and (bookmark_field in transformed_record):
                    last_dttm = transform_datetime(last_datetime)
                    bookmark_dttm = transform_datetime(transformed_record[bookmark_field])
                    # Keep only records whose bookmark is after the last_datetime
                    if bookmark_dttm:
                        if bookmark_dttm >= last_dttm:
                            write_record(stream_name, transformed_record, \
                                time_extracted=time_extracted)
                            counter.increment()
                else:
                    write_record(stream_name, transformed_record, time_extracted=time_extracted)
                    counter.increment()

        return max_bookmark_value, counter.value


# Sync a specific parent or child endpoint.
def sync_endpoint(
        client,
        config,
        catalog,
        state,
        start_date,
        stream_name,
        path,
        endpoint_config,
        bookmark_field,
        selected_streams=None,
        lock_period_days=None,
        date_window_days=None,
        parent=None,
        parent_id=None,
        account_sid=None):

    static_params = endpoint_config.get('params', {})
    bookmark_query_field_from = endpoint_config.get('bookmark_query_field_from')
    bookmark_query_field_to = endpoint_config.get('bookmark_query_field_to')
    data_key = endpoint_config.get('data_key', 'data')
    id_fields = endpoint_config.get('key_properties')
    lock_period_ind = endpoint_config.get('lock_period_ind', False)

    # Get the latest bookmark for the stream and set the last_integer/datetime
    last_datetime = get_bookmark(state, stream_name, start_date)
    max_bookmark_value = last_datetime
    LOGGER.info('stream: {}, bookmark_field: {}, last_datetime: {}'.format(
         stream_name, bookmark_field, last_datetime))

    # windowing: loop through date date_window_days date windows from last_datetime to now_datetime
    now_datetime = utils.now()
    if bookmark_query_field_from and bookmark_query_field_to:
        # lock_period_days: Attribution window, look-back window, or latency window. Number of
        #   days to ALWAYS look back, at a minimum, to account for delays in reconciling metrics.
        # lock_period_days from config, default = 60; passed to function from sync
        if lock_period_ind:
            if not lock_period_days:
                lock_period_days = 14
        else:
            lock_period_days = 0

        # date_window_days: Number of days in each date window
        # date_window_days from config, default = 30; passed to function from sync
        if stream_name == 'alerts':
            if date_window_days >= 30:
                date_window_days = 30

        # Set start window
        last_dttm = strptime_to_utc(last_datetime)
        start_window = now_datetime - timedelta(days=lock_period_days)
        if last_dttm < start_window:
            start_window = last_dttm

        # Set end window
        end_window = start_window + timedelta(days=date_window_days)
        if end_window > now_datetime:
            end_window = now_datetime
    else:
        start_window = strptime_to_utc(last_datetime)
        end_window = now_datetime
        diff_sec = (end_window - start_window).seconds
        date_window_days = math.ceil(diff_sec / (3600 * 24)) # round-up difference to days

    endpoint_total = 0

    while start_window < now_datetime:
        LOGGER.info('START Sync for Stream: {}{}'.format(
            stream_name,
            ', Date window from: {} to {}'.format(start_window, end_window) \
                if bookmark_query_field_from else ''))

        params = static_params # adds in endpoint specific, sort, filter params

        if bookmark_query_field_from and bookmark_query_field_to:
            params[bookmark_query_field_from] = strftime(start_window)[:10] # truncate date
            params[bookmark_query_field_to] = strftime(end_window)[:10] # truncate date

        # pagination: loop thru all pages of data using next (if not None)
        page = 1
        api_version = endpoint_config.get('api_version')
        if api_version in path:
            next_url = '{}{}'.format(endpoint_config.get('api_url'), path)
        else:
            next_url = '{}/{}/{}'.format(endpoint_config.get('api_url'), api_version, path)

        offset = 0
        limit = 500 # Default limit for Twilio API, unable to change this
        total_records = 0

        while next_url is not None:
            # Need URL querystring for 1st page; subsequent pages provided by next_url
            # querystring: Squash query params into string
            querystring = None
            if page == 1 and not params == {}:
                querystring = '&'.join(['%s=%s' % (key, value) for (key, value) in params.items()])
                # Replace <parent_id> in child stream params
                if parent_id:
                    querystring = querystring.replace('<parent_id>', parent_id)
            else:
                params = None
            LOGGER.info('URL for Stream {}: {}{}'.format(
                stream_name,
                next_url,
                '?{}'.format(querystring) if params else ''))

            # API request data
            data = client.get(
                url=next_url,
                path=path,
                params=querystring,
                endpoint=stream_name)

            # time_extracted: datetime when the data was extracted from the API
            time_extracted = utils.now()
            if not data or data is None or data == {}:
                total_records = 0
                break # No data results

            # Get pagination details
            pagination = data.get('meta', {}).get('pagination', {})
            api_total = int(pagination.get('total_results', 0))
            if pagination.get('next', {}).get('rel') == 'next':
                next_url = pagination.get('next', {}).get('href')
            else:
                next_url = None

            # Transform data with transform_json from transform.py
            # The data_key identifies the array/list of records below the <root> element
            # LOGGER.info('data = {}'.format(data)) # TESTING, comment out
            transformed_data = [] # initialize the record list
            data_list = []
            data_dict = {}
            if data_key in data:
                if isinstance(data[data_key], list):
                    transformed_data = transform_json(data, stream_name, data_key)
                elif isinstance(data[data_key], dict):
                    data_list.append(data[data_key])
                    data_dict[data_key] = data_list
                    transformed_data = transform_json(data_dict, stream_name, data_key)
            else: # data_key not in data
                if isinstance(data, list):
                    data_list = data
                    data_dict[data_key] = data_list
                    transformed_data = transform_json(data_dict, stream_name, data_key)
                elif isinstance(data, dict):
                    data_list.append(data)
                    data_dict[data_key] = data_list
                    transformed_data = transform_json(data_dict, stream_name, data_key)

            # LOGGER.info('transformed_data = {}'.format(transformed_data)) # TESTING, comment out
            if not transformed_data or transformed_data is None:
                LOGGER.info('No transformed data for data = {}'.format(data))
                total_records = 0
                break # No data results

            # Verify key id_fields are present
            for record in transformed_data:
                for key in id_fields:
                    if not record.get(key):
                        LOGGER.info('Stream: {}, Missing key {} in record: {}'.format(
                            stream_name, key, record))
                        raise RuntimeError

            # Process records and get the max_bookmark_value and record_count for the set of records
            if stream_name in selected_streams:
                max_bookmark_value, record_count = process_records(
                    catalog=catalog,
                    stream_name=stream_name,
                    records=transformed_data,
                    time_extracted=time_extracted,
                    bookmark_field=bookmark_field,
                    max_bookmark_value=max_bookmark_value,
                    last_datetime=last_datetime,
                    parent=parent,
                    parent_id=parent_id)
                LOGGER.info('Stream {}, batch processed {} records'.format(
                    stream_name, record_count))

            # Loop thru parent batch records for each children objects (if should stream)
            children = endpoint_config.get('children')
            if children:
                for child_stream_name, child_endpoint_config in children.items():
                    if child_stream_name in selected_streams:
                        LOGGER.info('START Syncing: {}'.format(child_stream_name))
                        write_schema(catalog, child_stream_name)
                        # For each parent record
                        for record in transformed_data:
                            i = 0
                            # Set parent_id
                            for id_field in id_fields:
                                if i == 0:
                                    parent_id_field = id_field
                                if id_field == 'id':
                                    parent_id_field = id_field
                                i = i + 1
                            parent_id = record.get(parent_id_field)

                            # sync_endpoint for child
                            LOGGER.info('START Sync for Stream: {}, parent_stream: {}, parent_id: {}'
                                        .format(child_stream_name, stream_name, parent_id))
                            # TODO: Document subresource URIs
                            child_path = record.get('_subresource_uris', {}).get(child_stream_name, None)
                            child_bookmark_field = next(iter(child_endpoint_config.get(
                                'replication_keys', [])), None)

                            if child_path:
                                child_total_records = sync_endpoint(
                                    client=client,
                                    config=config,
                                    catalog=catalog,
                                    state=state,
                                    start_date=start_date,
                                    stream_name=child_stream_name,
                                    path=child_path,
                                    endpoint_config=child_endpoint_config,
                                    bookmark_field=child_bookmark_field,
                                    selected_streams=selected_streams,
                                    lock_period_days=lock_period_days,
                                    date_window_days=date_window_days,
                                    parent=child_endpoint_config.get('parent'),
                                    parent_id=parent_id,
                                    account_sid=account_sid)
                            else:
                                LOGGER.info('No child stream {} for parent stream {} in subresource uris'
                                            .format(child_stream_name, stream_name))
                                child_total_records = 0
                            LOGGER.info(
                                'FINISHED Sync for Stream: {}, parent_id: {}, total_records: {}'\
                                    .format(child_stream_name, parent_id, child_total_records))
                            # End transformed data record loop
                        # End if child in selected streams
                    # End child streams for parent
                # End if children

            # Parent record batch
            # Adjust total_records w/ record_count, if needed
            if record_count > total_records:
                total_records = total_records + record_count
            else:
                total_records = api_total

            # to_rec: to record; ending record for the batch page
            to_rec = offset + limit
            if to_rec > total_records:
                to_rec = total_records

            LOGGER.info('Synced Stream: {}, page: {}, {} to {} of total records: {}'.format(
                stream_name,
                page,
                offset,
                to_rec,
                total_records))
            # Pagination: increment the offset by the limit (batch-size) and page
            offset = offset + limit
            page = page + 1
            # End page/batch - while next URL loop

        # Update the state with the max_bookmark_value for the stream date window
        # Twilio API does not allow page/batch sorting; bookmark written for date window
        if bookmark_field:
            write_bookmark(state, stream_name, max_bookmark_value)

        # Increment date window and sum endpoint_total
        start_window = end_window
        next_end_window = end_window + timedelta(days=date_window_days)
        if next_end_window > now_datetime:
            end_window = now_datetime
        else:
            end_window = next_end_window
        endpoint_total = endpoint_total + total_records
        # End date window

    # Return total_records (for all pages and date windows)
    return endpoint_total


# Currently syncing sets the stream currently being delivered in the state.
# If the integration is interrupted, this state property is used to identify
#  the starting point to continue from.
# Reference: https://github.com/singer-io/singer-python/blob/master/singer/bookmarks.py#L41-L46
def update_currently_syncing(state, stream_name):
    if (stream_name is None) and ('currently_syncing' in state):
        del state['currently_syncing']
    else:
        singer.set_currently_syncing(state, stream_name)
    singer.write_state(state)


def sync(client, config, catalog, state):
    if 'start_date' in config:
        start_date = config['start_date']

    # Get selected_streams from catalog, based on state last_stream
    #   last_stream = Previous currently synced stream, if the load was interrupted
    last_stream = singer.get_currently_syncing(state)
    LOGGER.info('last/currently syncing stream: {}'.format(last_stream))
    selected_streams = []
    for stream in catalog.get_selected_streams(state):
        selected_streams.append(stream.stream)
    LOGGER.info('selected_streams: {}'.format(selected_streams))

    if not selected_streams or selected_streams == []:
        return

    # Loop through endpoints in selected_streams
    for stream_name, endpoint_config in STREAMS.items():
        selected_streams_with_parents = selected_streams
        selected_streams_with_parents.append('accounts')
        if stream_name in selected_streams_with_parents:
            LOGGER.info('START Syncing: {}'.format(stream_name))
            write_schema(catalog, stream_name)
            update_currently_syncing(state, stream_name)
            path = endpoint_config.get('path', stream_name)
            bookmark_field = next(iter(endpoint_config.get('replication_keys', [])), None)
            total_records = sync_endpoint(
                client=client,
                config=config,
                catalog=catalog,
                state=state,
                start_date=start_date,
                stream_name=stream_name,
                path=path,
                endpoint_config=endpoint_config,
                bookmark_field=bookmark_field,
                selected_streams=selected_streams,
                lock_period_days=int(config.get('lock_period_days', '60')),
                date_window_days=int(config.get('date_window_days', '30')))

            update_currently_syncing(state, None)
            LOGGER.info('FINISHED Syncing: {}, total_records: {}'.format(
                stream_name,
                total_records))
