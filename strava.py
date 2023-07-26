import os
import time
import datetime
import urllib.request
from urllib.error import HTTPError
import json
import time
import re
from pprint import pprint
import re
import sys
from argparse import ArgumentParser

"""
This is a tool to extract data from strava APIs and save out two CSV files: one for lap level breakdown and another at activity level

Prior to data access the app will prompt you for API credentials if they are missing or the access_token is expired.
These settings are stored in a json file (.strava_settings.json)

Some settings can be configured via command line switches. See -h for options.
"""

SUPPORTED_ACTIVITY_TYPES = ["Run"]

MIN_LAP_DURATION_SECS = 10 # throw away laps smaller than this
COMBINE_WHITESPACE_REGEX = re.compile(r"\s+", re.MULTILINE)
EMOJI_REGEX = re.compile("["
    u"\U0001F600-\U0001F64F"  # emoticons
    u"\U0001F300-\U0001F5FF"  # symbols & pictographs
    u"\U0001F680-\U0001F6FF"  # transport & map symbols
    u"\U0001F1E0-\U0001F1FF"  # flags (iOS)
    u"\U00002500-\U00002BEF"  # chinese char
    u"\U00002702-\U000027B0"
    u"\U00002702-\U000027B0"
    u"\U000024C2-\U0001F251"
    u"\U0001f926-\U0001f937"
    u"\U00010000-\U0010ffff"
    u"\u2640-\u2642" 
    u"\u2600-\u2B55"
    u"\u200d"
    u"\u23cf"
    u"\u23e9"
    u"\u231a"
    u"\ufe0f"  # dingbats
    u"\u3030"
    "]+", re.UNICODE)

class Settings:
    def __init__(self, filename, access_token = None, client_id = None, client_code = None,  client_secret = None):
        self.filename = filename
        self.access_token = access_token
        self.client_id = client_id
        self.client_code = client_code
        self.client_secret = client_secret
        
    @staticmethod
    def load_from_json(filename):
        def _get_value_or_none(key, dictionary):
            return dictionary[key] if key in dictionary else None
        
        if not os.path.exists(filename):
            return Settings(filename)
        
        with open(filename, "r") as file:
            print("Loading settings from {}".format(filename))
            data = json.load(file)
            return Settings(filename, 
                _get_value_or_none("access_token", data),
                _get_value_or_none("client_id", data),
                _get_value_or_none("client_code", data),
                _get_value_or_none("client_secret", data))
    
    def save_to_file(self):
        data = {key: self.__dict__[key] for key in self.__dict__.keys() if key != "filename"}
        json_data = json.dumps(data, indent=2)
        with open(self.filename, "w") as file:
            print("Saving settings to {}".format(self.filename))
            file.write(json_data)
            
    def get_headers(self):
        return {"Authorization": "Bearer {}".format(self.access_token)}

class Activity:
    def __init__(self, id):
        self.id = id
        
    @staticmethod
    def from_json(data):
        if not data["type"] in SUPPORTED_ACTIVITY_TYPES:
            return None
        
        return Activity(data["id"])
        
class ActivityDetail:
    _activity_property_names = []
    
    _lap_property_names = ["name", "start_date", "elapsed_time", "moving_time", "distance", "total_elevation_gain", "average_speed", "average_heartrate"]
    _lap_property_display_names = ["lap_name", "start_date", "elapsed_time_secs", "moving_time_secs", "distance_metres", "total_elevation_gain_metres", "average_speed_kms_per_hr", "average_heartrate"]

    def __init__(self, data, lap_properties):
        
        self.activity_metadata = { 
            "activity_id": ActivityDetail._get_simple_value("id", data),
            "activity_name": ActivityDetail._get_simple_value("name", data),
            "activity_description": ActivityDetail._get_simple_value("description", data),
            "activity_shoes": data["gear"]["name"] if "gear" in data else "",
            "activity_calories": ActivityDetail._get_simple_value("calories", data),
            "activity_average_cadence": ActivityDetail._get_simple_value("average_cadence", data),
            "activity_average_temp": ActivityDetail._get_simple_value("average_temp", data),
            "activity_moving_time_secs": ActivityDetail._get_simple_value("moving_time", data),
            "activity_elapsed_time_secs": ActivityDetail._get_simple_value("elapsed_time", data),
            "activity_distance_metres": ActivityDetail._get_simple_value("distance", data),
            "activity_total_elevation_gain_metres": ActivityDetail._get_simple_value("total_elevation_gain", data),
        }
        self.lap_properties = lap_properties
            
    @staticmethod
    def from_json(data):        
        lap_properties = []

        #manual activity
        if not "laps" in data:
            lap_properties.append({property_name: ActivityDetail._get_simple_value(property_name, data) for property_name in ActivityDetail._lap_property_names})
            return ActivityDetail(data, lap_properties)
        
        has_user_recorded_laps = len(data["laps"]) > 1
        if has_user_recorded_laps:
            for lap in [lap for lap in data["laps"] if lap["elapsed_time"] > MIN_LAP_DURATION_SECS]:
                lap_properties.append({property_name: ActivityDetail._get_simple_value(property_name, lap) for property_name in ActivityDetail._lap_property_names})
            return ActivityDetail(data, lap_properties)
        
        start_date = data["start_date"]
        total_elevation = data["total_elevation_gain"]
        total_distance = data["distance"]
        for i, lap in enumerate([lap for lap in data["splits_metric"] if lap["elapsed_time"] > MIN_LAP_DURATION_SECS]):
            lap_elevation = 0 if total_distance == 0 else total_elevation * lap["distance"] / total_distance #pro rata
            lap_properties.append({property_name: ActivityDetail._get_auto_lap_value(property_name, lap, i, start_date, lap_elevation) for property_name in ActivityDetail._lap_property_names})
        return ActivityDetail(data, lap_properties)
    
    @staticmethod
    def _get_simple_value(key, dictionary):
        if not key in dictionary:
            return ""
        if key == "average_speed":
            return str(dictionary[key] * 1.6) # miles to kms / hr
        
        value = str(dictionary[key])
        value = value.replace(",", ";")
        value = EMOJI_REGEX.sub("", value) 
        value = COMBINE_WHITESPACE_REGEX.sub("", value)
        return value.strip()
    
    @staticmethod
    def _get_auto_lap_value(key, dictionary, index, start_date, lap_elevation):
        # https://stackoverflow.com/questions/33404752/removing-emojis-from-a-string-in-python
        if key == "name":
            return "Auto km lap {}".format(index + 1)
        if key == "start_date":
            return str(start_date)
        if key == "total_elevation_gain":
            return str(lap_elevation)
        return ActivityDetail._get_simple_value(key, dictionary)
    
    def to_lap_csv_header(self):
        activity_property_keys = self._get_activity_metadata_keys()    
        return ",".join(activity_property_keys + ActivityDetail._lap_property_display_names)
        
    def to_lap_csvs(self):
        ret = []
        activity_property_keys = self._get_activity_metadata_keys()
        activity_values = [self.activity_metadata[key] for key in activity_property_keys]
        for lap in self.lap_properties:
            values = activity_values + [lap[key] for key in ActivityDetail._lap_property_names]
            ret.append(",".join(values))
        return ret

    def to_summary_csv_header(self):
        activity_property_keys = self._get_activity_metadata_keys()
        return ",".join(activity_property_keys)
        
    def to_summary_csvs(self):
        activity_property_keys = self._get_activity_metadata_keys()
        return [",".join(self.activity_metadata[key] for key in activity_property_keys)]
    
    def _get_activity_metadata_keys(self):
        keys = [key for key in self.activity_metadata.keys()]
        keys.sort()   
        return keys

def write_to_csv(activities, output_folder, min_date, max_date):
    def _get_filename(prefix):
        if min_date == max_date:
            return "{}_{}.csv".format(prefix, min_date.strftime("%Y-%m-%d"))
        return "{}_{}_to_{}.csv".format(prefix, min_date.strftime("%Y-%m-%d"), max_date.strftime("%Y-%m-%d"))

    if not activities:
        return

    with open("{}/{}".format(output_folder, _get_filename("activity_summary")), "w", encoding="utf-8") as file:
        print("Writing {} activities to {}".format(len(activities), file.name))
        file.write(activities[0].to_summary_csv_header() + "\n")
        for activity in activities:
            file.writelines([line + "\n" for line in activity.to_summary_csvs()])
    
    with open("{}/{}".format(output_folder, _get_filename("activity_laps")), "w", encoding="utf-8") as file:
        print("Writing {} activities to {}".format(len(activities), file.name))
        file.write(activities[0].to_lap_csv_header() + "\n")
        for activity in activities:
            file.writelines([line + "\n" for line in activity.to_lap_csvs()])

def get_activities(min_date, max_date, headers):    
    def _get_epoc_date(dt):
        epoch = datetime.datetime(1970, 1, 1, 0, 0, 0)
        return int((dt-epoch).total_seconds())
    
    def _get_activities_impl():
        try: 
            # reference: https://developers.strava.com/docs/reference/#api-Activities-getLoggedInAthleteActivities
            
            #TODO: hook up before and after..
            before = _get_epoc_date(max_date) # Integer | An epoch timestamp to use for filtering activities that have taken place before a certain time. (optional)
            after = _get_epoc_date(min_date) # Integer | An epoch timestamp to use for filtering activities that have taken place after a certain time. (optional)
            page = 1 # Integer | Page number. Defaults to 1. (optional)
            per_page = 50 # Integer | Number of items per page. Defaults to 30. (optional) (default to 30)
        
            ret = []
            while True:            
                api_url = "https://www.strava.com/api/v3/athlete/activities?page={}&per_page={}&before={}&after={}".format(page, per_page, before, after)
                print(api_url)
                request = urllib.request.Request(api_url, headers=headers)
                response = urllib.request.urlopen(request)
                api_response = json.loads(response.read())
                activities = [Activity.from_json(a) for a in api_response]
                if not activities:
                    break
                ret += [a for a in activities if a]
                page += 1
            return ret
        except Exception as ex:
            print("Unable to get activities {}".format(ex))
            raise
        
    for i in range(1, 4):
        try:
            return _get_activities_impl()
        except HTTPError as ex:
            if ex.code == 429:
                sleep_mins = pow(2, i)
                print("We got throttled.. sleeping for {} mins".format(sleep_mins))
                time.sleep(sleep_mins * 60)    
        
def get_activity_detail(activity, headers):    
    def _get_activity_detail_impl(activity):    
        try:    
            # reference: https://developers.strava.com/docs/reference/#api-Activities-getActivityById
            api_url = "https://www.strava.com/api/v3/activities/{}".format(activity.id)
            print(api_url)
            request = urllib.request.Request(api_url, headers=headers)
            response = urllib.request.urlopen(request)
            api_response = json.loads(response.read())
            return ActivityDetail.from_json(api_response)
        except Exception as ex:
            print("Unable to get activity details {}".format(ex))
            raise
        
    last_ex = None
    for i in range(1, 4):
        try:
            return _get_activity_detail_impl(activity)
        except HTTPError as ex:
            if ex.code == 429:
                sleep_mins = pow(2, i)
                print("We got throttled.. sleeping for {} mins".format(sleep_mins))
                time.sleep(sleep_mins * 60)
    if last_ex:
        print("*** unable to load activity {} ***".format(activity.id))
    return None

def load_settings():
    """ thanks to some code on the internet :) 
    https://www.grace-dev.com/python-apis/strava-api/
    """
    def _has_token_expired():
        try:
            print("Testing if token is still valid..")
            api_url = "https://www.strava.com/api/v3/athlete"
            request = urllib.request.Request(api_url, headers=settings.get_headers())
            response = urllib.request.urlopen(request)            
            return False
        except HTTPError as ex:
            if ex.code == 401:
                print("token expired")
                return True
            raise
    
    def _get_new_token():
        request_url = "http://www.strava.com/oauth/authorize?client_id={}&response_type=code&redirect_uri=http://localhost/&approval_prompt=force&scope=profile:read_all,activity:read_all".format(settings.client_id)
        print("Click here: {}".format(request_url))
        print("Please authorize the app and copy&paste below the generated code!")
        print("P.S: you can find the code in the URL")
        settings.client_code = input("Insert the code from the url:")
        if not settings.client_code:
            raise Exception("client code can not be empty")
        settings.save_to_file()

        request_data = {
            "client_id": settings.client_id,
            "client_secret": settings.client_secret,
            "code": settings.client_code,
            "grant_type": 'authorization_code'
        }
        headers = {"Content-Type": "application/json; charset=utf-8"}
        request_bytes = json.dumps(request_data).encode("utf-8")
        request = urllib.request.Request("https://www.strava.com/api/v3/oauth/token", data=request_bytes, headers=headers)
        response = urllib.request.urlopen(request)
        api_response = json.loads(response.read())
        return api_response["access_token"]
    
    file_parts = os.path.split(__file__)
    settings_file = "{}\{}".format(file_parts[0], ".{}_settings.json".format(os.path.splitext(file_parts[1])[0]))
    settings = Settings.load_from_json(settings_file)
    
    if not settings.client_id:
        settings.client_id = input("Enter your client id from https://www.strava.com/settings/api :")
        if not settings.client_id:
            raise Exception("client id can not be empty")
        settings.save_to_file()
        
    if not settings.client_secret:
        settings.client_secret = input("Enter your client secret from https://www.strava.com/settings/api :")
        if not settings.client_secret:
            raise Exception("client client secret can not be empty")
        settings.save_to_file()
    
    if not settings.client_code or _has_token_expired():
        settings.access_token = _get_new_token()
        settings.save_to_file()    
    return settings

def parse_args():
    default_output_folder = "c:/temp"
    default_min_date = datetime.datetime.today() - datetime.timedelta(days=7)
    default_max_date = datetime.datetime.today()    
    
    parser = ArgumentParser()
    parser.add_argument("-o", "--output_folder", 
                        dest="output_folder",
                        help="Directory where files are written to.", 
                        metavar="FILE", 
                        default=default_output_folder,
                        required=False)
    parser.add_argument("-s", "--start_date",
                        type=lambda s: datetime.datetime.strptime(s, '%Y-%m-%d'), dest="start_date", default=default_min_date,
                        help="Earliest date to find runs",
                        required=False)
    parser.add_argument("-e", "--end_date",
                        type=lambda s: datetime.datetime.strptime(s, '%Y-%m-%d'), dest="end_date", default=default_max_date,
                        help="Latest date to find runs",
                        required=False)    
    
    args = parser.parse_args(sys.argv[1:])    
    return args
    
args = parse_args()
settings = load_settings()
headers = settings.get_headers()
activities = get_activities(args.start_date, args.end_date, headers) or []
print("{} activities found".format(len(activities)))
activity_details = [get_activity_detail(activity, headers) for activity in activities]
resolved_activity_details = [details for details in activity_details if details]
print("{} activity details found".format(len(resolved_activity_details)))
write_to_csv(resolved_activity_details, args.output_folder, args.start_date, args.end_date)
    
