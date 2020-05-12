from re import sub
from decimal import Decimal
import datetime
import pytz
import singer
from singer.utils import strftime

LOGGER = singer.get_logger()

# Subresource URI dict to array; same with links
# Dates: date_updated, date_created, date_sent, date_fired, format: "Sat, 29 Sep 2019 19:45:43 +0000"
# Dates: start_date, end_date to datetime
# lowercase field name keys


def string_to_decimal(data_dict, data_key, number_fields):
    new_dict = data_dict
    if number_fields:
        i = 0
        for record in data_dict[data_key]:
            for number_field in number_fields:
                string_val = record.get(number_field)
                if string_val:
                    dec_val = Decimal(sub(r'[^\d.]', '', string_val))
                    new_dict[data_key][i][number_field] = dec_val
            i = i + 1
    return new_dict


def est_to_utc_datetime(data_dict, data_key, datetime_fields):
    timezone = pytz.timezone('US/Eastern')
    new_dict = data_dict
    if datetime_fields:
        i = 0
        for record in data_dict[data_key]:
            for datetime_field in datetime_fields:
                est_datetime_val = record.get(datetime_field)
                if est_datetime_val:
                    if est_datetime_val == '0000-00-00 00:00:00':
                        utc_datetime = None
                    else:
                        try:
                            est_datetime = timezone.localize(datetime.datetime.strptime(
                                est_datetime_val, "%Y-%m-%d %H:%M:%S"))
                            utc_datetime = strftime(timezone.normalize(est_datetime).astimezone(
                                pytz.utc))
                        except ValueError as err:
                            LOGGER.warning('Value Error: {}'.format(err))
                            LOGGER.warning('Invalid Date: {}'.format(est_datetime_val))
                            LOGGER.warning('record: {}'.format(record))
                            utc_datetime = None
                    new_dict[data_key][i][datetime_field] = utc_datetime
            i = i + 1
    return new_dict


def date_to_datetime(data_dict, data_key):
    new_dict = data_dict
    i = 0
    for record in data_dict[data_key]:
        date_val = record.get('date')
        if date_val:
            if date_val == '0000-00-00':
                datetime_val = None
            else:
                datetime_val = '{}T00:00:00Z'.format(date_val)
            new_dict[data_key][i]['datetime'] = datetime_val
        i = i + 1
    return new_dict

# Run all transforms: ...
def transform_json(data_dict, stream_name, data_key):
    date_to_datetime_ind = False
    datetime_fields = []
    number_fields = []
    if stream_name in ('creative_advanced', 'creative_banner', 'creative_coupon', 'creative_text'):
        datetime_fields = ['start_date', 'end_date', 'view_date', 'created', 'modified']
        number_fields = []
    elif stream_name == 'creative_generic':
        datetime_fields = ['modified']
        number_fields = []
    elif stream_name == 'creative_product':
        datetime_fields = []
        number_fields = ['price', 'price_retail', 'price_sale', 'price_shipping']
    elif stream_name in ('creative_performance', 'creative_performance_by_publisher'):
        date_to_datetime_ind = True
        datetime_fields = []
        number_fields = ['click_through_rate', 'sales', 'earnings_per_click', 'commission']
    elif stream_name == 'publisher_performance':
        date_to_datetime_ind = True
        datetime_fields = []
        number_fields = ['sale_lead_amount', 'earnings_per_click', 'bonus_amount', 'total_commission', \
            'site_bonus', 'site_commission', 'publisher_bonus', 'publisher_commission']
    elif stream_name == 'transaction_details':
        datetime_fields = ['sale_date']
        number_fields = ['sale_amount', 'commission', 'commission_publisher', 'commission_site']
    elif stream_name == 'transaction_history':
        datetime_fields = ['sale_date', 'process_date']
        number_fields = ['commission', 'publisher_commission', 'site_commission', 'sale_amount']
    else:
        datetime_fields = []
        number_fields = []

    # Transform string number fields to decimal
    number_dict = string_to_decimal(data_dict, data_key, number_fields)

    # Transform EST to UTC datetimes
    datetime_dict = est_to_utc_datetime(number_dict, data_key, datetime_fields)

    # Transform date to datetime
    if date_to_datetime_ind:
        date_dict = date_to_datetime(datetime_dict, data_key)
    else:
        date_dict = datetime_dict

    return date_dict[data_key]
