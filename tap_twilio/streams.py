# streams: API URL endpoints to be called
# properties:
#   <root node>: Plural stream name for the endpoint
#   path: API endpoint relative path, when added to the base URL, creates the full path
#   key_properties: Primary key field(s) for the object endpoint
#   replication_method: FULL_TABLE or INCREMENTAL
#   replication_keys: bookmark_field(s), typically a date-time, used for filtering the results
#        and setting the state
#   params: Query, sort, and other endpoint specific parameters
#   data_key: JSON element containing the records for the endpoint
#   bookmark_query_field: Typically a date-time field used for filtering the query
#   bookmark_type: Data type for bookmark, integer or datetime
#   children: A collection of child endpoints (where the endpoint path includes the parent id)
#   parent: On each of the children, the singular stream name for parent element

STREAMS = {
    # Reference: https://www.twilio.com/docs/usage/api/account#read-multiple-account-resources

    'workspaces': {
        'api_url': 'https://taskrouter.twilio.com',
        'api_version': 'v1',
        'path': 'Workspaces',
        'data_key': 'workspaces',
        'key_properties': ['sid'],
        'replication_method': 'FULL_TABLE',
        'replication_keys': ['date_updated'],
        'params': {},
        'pagination': 'root',
        'children': {
            'activities': {
                'api_url': '',  # https://taskrouter.twilio.com',
                'api_version': 'v1',
                'path': 'Workspaces/{ParentId}/Activities',
                'data_key': 'activities',
                'key_properties': ['sid'],
                'replication_method': 'FULL_TABLE',
                'replication_keys': ['date_updated'],
                'params': {},
                'pagination': 'root',
            },
            'workers': {
                'api_url': '',  # https://taskrouter.twilio.com',
                'api_version': 'v1',
                'path': 'Workspaces/{ParentId}/Workers',
                'data_key': 'workers',
                'key_properties': ['sid'],
                'replication_method': 'FULL_TABLE',
                'replication_keys': ['date_updated'],
                'params': {},
                'pagination': 'none',
            },
            'workflows': {
                'api_url': '',#https://taskrouter.twilio.com',
                'api_version': 'v1',
                'path': 'Workspaces/{ParentId}/Workflows',
                'data_key': 'workflows',
                'key_properties': ['sid'],
                'replication_method': 'FULL_TABLE',
                'replication_keys': ['date_updated'],
                'params': {},
                'pagination': 'root',
            },
        },
    },
}


# De-nest children nodes for Discovery mode
def flatten_streams():
    flat_streams = {}
    # Loop through parents
    for stream_name, endpoint_config in STREAMS.items():
        flat_streams[stream_name] = {
            'key_properties': endpoint_config.get('key_properties'),
            'replication_method': endpoint_config.get('replication_method'),
            'replication_keys': endpoint_config.get('replication_keys')
        }
        # Loop through children
        children = endpoint_config.get('children')
        if children:
            for child_stream_name, child_endpoint_config in children.items():
                flat_streams[child_stream_name] = {
                    'key_properties': child_endpoint_config.get('key_properties'),
                    'replication_method': child_endpoint_config.get('replication_method'),
                    'replication_keys': child_endpoint_config.get('replication_keys'),
                    'parent_stream': stream_name
                }
                # Loop through grand-children
                grandchildren = child_endpoint_config.get('children')
                if grandchildren:
                    for grandchild_stream_name, grandchild_endpoint_config in \
                            grandchildren.items():
                        flat_streams[grandchild_stream_name] = {
                            'key_properties': grandchild_endpoint_config.get('key_properties'),
                            'replication_method': grandchild_endpoint_config.get(
                                'replication_method'),
                            'replication_keys': grandchild_endpoint_config.get('replication_keys'),
                            'parent_stream': child_stream_name,
                            'grandparent_stream': stream_name
                        }
    return flat_streams
