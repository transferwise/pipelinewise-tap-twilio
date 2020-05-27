from re import sub
from decimal import Decimal
import datetime
import pytz
import singer
from singer.utils import strftime

LOGGER = singer.get_logger()

# TODO: pare this down
# Subresource URI dict to array; same with links
# Dates: date_updated, date_created, date_sent, date_fired, format: "Sat, 29 Sep 2019 19:45:43 +0000"
# Dates: start_date, end_date to datetime
# lowercase field name keys


def subresources_to_array(data_dict, data_key):
    new_dict = data_dict
    i = 0
    for record in data_dict[data_key]:
        subresources = record.get('subresource_uris', None)
        new_dict[data_key][i]['_subresource_uris'] = record.get('subresource_uris', None)
        if subresources:
            subresource_mappings = []
            for subresource_name, subresource_uri in subresources.items():
                subresource_mappings.append({'subresource': subresource_name, 'uri': subresource_uri})
            new_dict[data_key][i]['subresource_uris'] = subresource_mappings
        i = i + 1

    return new_dict


# Run all transforms: ...
def transform_json(data_dict, stream_name, data_key):
    transformed_dict = subresources_to_array(data_dict, data_key)

    return transformed_dict[data_key]
