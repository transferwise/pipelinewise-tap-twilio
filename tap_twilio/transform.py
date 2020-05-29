import singer

LOGGER = singer.get_logger()


def subresources_to_array(data_dict, data_key):
    new_dict = data_dict
    i = 0
    for record in data_dict[data_key]:
        subresources = record.get('subresource_uris', None)
        if subresources:
            new_dict[data_key][i]['_subresource_uris'] = record.get('subresource_uris')
            subresource_mappings = []
            for subresource_name, subresource_uri in subresources.items():
                subresource_mappings.append(
                    {'subresource': subresource_name, 'uri': subresource_uri})
            new_dict[data_key][i]['subresource_uris'] = subresource_mappings
        i = i + 1

    return new_dict


# Run all transforms: ...
def transform_json(data_dict, data_key):
    transformed_dict = subresources_to_array(data_dict, data_key)

    return transformed_dict[data_key]
