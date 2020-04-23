#!/usr/bin/env python3

#import libraries needed in this program
import requests, time, threading, os, json, logging, sys, argparse, logging.handlers
from datetime import datetime, date, timedelta
from pathlib import Path

#a flag to determine whether the user wants to exit the program, so can handle the program exit gracefully
is_exit = False

#determine how many logpush process are running
num_of_running_thread = 0

#define the timestamp format that we supply to Cloudflare API
timestamp_format = "rfc3339"

#the prefix name of the Elasticsearch ingest pipeline and the logfile name
pipeline_name_prefix = "cloudflare-pipeline-"
logfile_name_prefix = "cf_logs"

#initialize the variables to empty string, so the other parts of the program can access it
path = zone_id = access_token = username = password = sample_rate = port = ""

#the default value for the interval between each logpull process
interval = 60.0

#specify the number of attempts to retry in the event of error
retry_attempt = 5

#by default, 
#raw logs will be stored on local storage
#weekly pipeline will be used by default (unless daily pipeline is explicitly defined)
no_store = daily_pipeline = False

'''
Specify the fields for the logs

The following fields are available: CacheCacheStatus,CacheResponseBytes,CacheResponseStatus,CacheTieredFill,ClientASN,ClientCountry,ClientDeviceType,ClientIP,ClientIPClass,ClientRequestBytes,ClientRequestHost,ClientRequestMethod,ClientRequestPath,ClientRequestProtocol,ClientRequestReferer,ClientRequestURI,ClientRequestUserAgent,ClientSSLCipher,ClientSSLProtocol,ClientSrcPort,ClientXRequestedWith,EdgeColoCode,EdgeColoID,EdgeEndTimestamp,EdgePathingOp,EdgePathingSrc,EdgePathingStatus,EdgeRateLimitAction,EdgeRateLimitID,EdgeRequestHost,EdgeResponseBytes,EdgeResponseCompressionRatio,EdgeResponseContentType,EdgeResponseStatus,EdgeServerIP,EdgeStartTimestamp,FirewallMatchesActions,FirewallMatchesRuleIDs,FirewallMatchesSources,OriginIP,OriginResponseBytes,OriginResponseHTTPExpires,OriginResponseHTTPLastModified,OriginResponseStatus,OriginResponseTime,OriginSSLProtocol,ParentRayID,RayID,SecurityLevel,WAFAction,WAFFlags,WAFMatchedVar,WAFProfile,WAFRuleID,WAFRuleMessage,WorkerCPUTime,WorkerStatus,WorkerSubrequest,WorkerSubrequestCount,ZoneID
'''
fields = "CacheCacheStatus,CacheResponseBytes,CacheResponseStatus,CacheTieredFill,ClientASN,ClientCountry,ClientDeviceType,ClientIP,ClientIPClass,ClientRequestBytes,ClientRequestHost,ClientRequestMethod,ClientRequestPath,ClientRequestProtocol,ClientRequestReferer,ClientRequestURI,ClientRequestUserAgent,ClientSSLCipher,ClientSSLProtocol,ClientSrcPort,ClientXRequestedWith,EdgeColoCode,EdgeColoID,EdgeEndTimestamp,EdgePathingOp,EdgePathingSrc,EdgePathingStatus,EdgeRateLimitAction,EdgeRateLimitID,EdgeRequestHost,EdgeResponseBytes,EdgeResponseCompressionRatio,EdgeResponseContentType,EdgeResponseStatus,EdgeServerIP,EdgeStartTimestamp,FirewallMatchesActions,FirewallMatchesRuleIDs,FirewallMatchesSources,OriginIP,OriginResponseBytes,OriginResponseHTTPExpires,OriginResponseHTTPLastModified,OriginResponseStatus,OriginResponseTime,OriginSSLProtocol,ParentRayID,RayID,SecurityLevel,WAFAction,WAFFlags,WAFMatchedVar,WAFProfile,WAFRuleID,WAFRuleMessage,WorkerCPUTime,WorkerStatus,WorkerSubrequest,WorkerSubrequestCount,ZoneID"

#create a logging object for logging purposes
logger = logging.getLogger()

#the default logging level is INFO, which is one level higher than DEBUG
logger.setLevel(logging.INFO)
#create a handler to write logs to local storage, and automatically rotate them on a hourly basis. maximum backup count is 120
Path("/var/log/cf_elk_push/").mkdir(parents=True, exist_ok=True)
handler_file = logging.handlers.TimedRotatingFileHandler("/var/log/cf_elk_push/push.log", when='H', interval=1, backupCount=120, utc=False, encoding="utf-8")
#create a handler to print logs on terminal
handler_console = logging.StreamHandler()

#define the format of the logs for any logging event occurs
formatter = logging.Formatter("[%(levelname)s] %(message)s")
#set the log format for both handlers
handler_file.setFormatter(formatter)
handler_console.setFormatter(formatter)
#finally, add both handlers to the logger
logger.addHandler(handler_file)
logger.addHandler(handler_console)

'''
This is the starting point of the program. It will initialize the parameters supplied by the user and save it in a variable.
Help(welcome) message will be displayed if the user specifies -h or --help as the parameter.
If required parameters are not given by the user, an error message will be displayed to the user and the program will exit.
'''
def initialize_arg():
    
    global path, zone_id, access_token, username, password, sample_rate, interval, no_store, logger, daily_pipeline, port, logfile_name_prefix
    
    welcome_msg = "A utility to pull logs from Cloudflare, process it and push them to Elasticsearch."

    #create an argparse object with the welcome message as the description
    parser = argparse.ArgumentParser(description=welcome_msg)
    
    #specify which arguments are available to use in this program. The usage of the arguments will be printed when the user tells the program to display help message.
    parser.add_argument("-z", "--zone", help="Specify the Cloudflare Zone ID, if CF_ZONE_ID environment variable not set. This will override CF_ZONE_ID variable.")
    parser.add_argument("-t", "--token", help="Specify your Cloudflare Access Token, if CF_TOKEN environment variable not set. This will override CF_TOKEN variable.")
    parser.add_argument("-u", "--username", help="Specify the username to push logs to Elasticsearch, if ELASTIC_USERNAME environment variable not set. This will override ELASTIC_USERNAME variable.")
    parser.add_argument("-p", "--password", help="Specify the password to push logs to Elasticsearch, if ELASTIC_PASSWORD environment variable not set. This will override ELASTIC_PASSWORD variable.")
    parser.add_argument("-P", "--port", help="Specify the port that is listening by Elasticsearch. Default is port 9200.", default="9200")
    parser.add_argument("-r", "--rate", help="Specify the log sampling rate from 0.01 to 1. Default is 1.", default="1")
    parser.add_argument("-i", "--interval", help="Specify the interval between each logpull in seconds. Default is 60 seconds.", default=60.0, type=float)
    parser.add_argument("--path", help="Specify the path to store logs. By default, it will save to /var/log/cf_logs/", default="/var/log/cf_logs/")
    parser.add_argument("--prefix", help="Specify the prefix name of the logfile being stored on local storage. By default, the file name will begins with cf_logs.", default="cf_logs")
    parser.add_argument("--daily-pipeline", help="Daily ingest pipeline will be used instead of the default Weekly ingest pipeline, if specified.", action="store_true")
    parser.add_argument("--no-store", help="Instruct the program not to store a copy of raw logs on local storage.", action="store_true")
    parser.add_argument("--debug", help="Enable debugging functionality.", action="store_true")
    parser.add_argument("-v", "--version", help="Show program version.", action="version", version="Version 1.01")
    
    #parse the parameters supplied by the user, and check whether the parameters match the one specified above
    #if it does not match, an error message will be given to the user and the program will exit
    args = parser.parse_args()
    
    #enable debugging if specified by the user
    if args.debug is True:
        logger.setLevel(logging.DEBUG)
    
    #take the "path" parameter given by the user and assign it to a variable
    path = args.path
    
    #check whether Zone ID is given by the user via the parameter. If not, check the environment variable.
    #the Zone ID given via the parameter will override the Zone ID inside environment variable.
    #if no Zone ID is given, an error message will be given to the user and the program will exit
    if args.zone:
        zone_id = args.zone
    elif os.getenv("CF_ZONE_ID"):
        zone_id = os.getenv("CF_ZONE_ID")
    else:
        logger.critical(str(datetime.now()) + " --- Please specify your Cloudflare Zone ID.")
        sys.exit(2)
        
    #check whether Cloudflare Access Token is given by the user via the parameter. If not, check the environment variable.
    #the Cloudflare Access Token given via the parameter will override the Cloudflare Access Token inside environment variable.
    #if no Cloudflare Access Token is given, an error message will be given to the user and the program will exit
    if args.token:
        access_token = args.token
    elif os.getenv("CF_TOKEN"):
        access_token = os.getenv("CF_TOKEN")
    else:
        logger.critical(str(datetime.now()) + " --- Please specify your Cloudflare Access Token.")
        sys.exit(2)
        
    #check whether Elasticsearch username is given by the user via the parameter. If not, check the environment variable.
    #the Elasticsearch username given via the parameter will override the Elasticsearch username inside environment variable.
    #if no Elasticsearch username is given, an error message will be given to the user and the program will exit
    if args.username:
        username = args.username
    elif os.getenv("ELASTIC_USERNAME"):
        username = os.getenv("ELASTIC_USERNAME")
    else:
        logger.critical(str(datetime.now()) + " --- Please specify your Elasticsearch username.")
        sys.exit(2)
        
    #check whether Elasticsearch password is given by the user via the parameter. If not, check the environment variable.
    #the Elasticsearch password given via the parameter will override the Elasticsearch username inside environment variable.
    #if no Elasticsearch password is given, an error message will be given to the user and the program will exit
    if args.password:
        password = args.password
    elif os.getenv("ELASTIC_PASSWORD"):
        password = os.getenv("ELASTIC_PASSWORD")
    else:
        logger.critical(str(datetime.now()) + " --- Please specify your Elasticsearch password.")
        sys.exit(2)
    
    #check whether the port number is a valid port number, if not return an error message and exit
    if int(args.port) <= 65535 and int(args.port) >= 1:
        port = args.port
    else:
        logger.critical(str(datetime.now()) + " --- Invalid port number specified. Please specify a value between 1 and 65535.")
        sys.exit(2)
    
    #check whether the sample rate is valid, if not return an error message and exit
    try:
        #the value should not more than two decimal places
        if len(args.rate.split(".", 1)[1]) > 2:
            logger.critical(str(datetime.now()) + " --- Invalid sample rate specified. Please specify a value between 0.01 and 1, and only two decimal places allowed.")
            sys.exit(2)
    except IndexError:
        #sometimes the user may specify 1 as the value, so we need to handle the exception for value with no decimal places
        pass
    if float(args.rate) <= 1.0 and float(args.rate) >= 0.01:
        sample_rate = args.rate
    else:
        logger.critical(str(datetime.now()) + " --- Invalid sample rate specified. Please specify a value between 0.01 and 1, and only two decimal places allowed.")
        sys.exit(2)
    
    #take the port number, sample rate, interval, store logs and pipeline setting parameter given by the user and assign it to a variable
    interval = args.interval
    no_store = args.no_store
    daily_pipeline = args.daily_pipeline
    logfile_name_prefix = args.prefix
    
    
'''
This method will be invoked after initialize_arg().
This method is to verify whether the Cloudflare Zone ID, Cloudflare Access Token, Elasticsearch username and password given by the user is valid.
If it is not valid, an error message will be given to the user and the program will exit
'''
def verify_credential():
    
    global logger, username, password, daily_pipeline
    
    #specify the Cloudflare API URL to check the Zone ID and Access Token
    url = "https://api.cloudflare.com/client/v4/zones/" + zone_id + "/logs/received"
    headers = {"Authorization": "Bearer " + access_token, "Content-Type": "application/json"}
    
    #make a HTTP request to the Cloudflare API
    r = requests.get(url, headers=headers)
    r.encoding = "utf-8"
    
    #if there's an error, Cloudflare API will return a JSON object to indicate the error
    #and if it's not, a plain text will be returned instead
    #the try except block is to catch any errors raised by json.loads(), in case Cloudflare is not returning JSON object
    try:
        response = json.loads(r.text)
        if response["success"] is False:
            logger.critical(str(datetime.now()) + " --- Failed to authenticate with Cloudflare API. Please check your Zone ID and Cloudflare Access Token.")
            sys.exit(2)
    except json.JSONDecodeError:
        #a non-JSON object returned by Cloudflare indicates that authentication successful
        pass
    
    #specify the Elasticsearch API URL to check the username and password. it also checks whether the ingest pipeline exists in the Elasticsearch
    url = "http://localhost:" + port + "/_ingest/pipeline/" + pipeline_name_prefix + ("daily" if daily_pipeline is True else "weekly")
    auth_elastic = (username, password)
    
    #make a HTTP request to the Elasticsearch API
    try:
        r = requests.get(url, auth=auth_elastic)
    except requests.exceptions.ConnectionError:
        #in the event that the Elasticsearch server is unable to connect, an error message will display to the user and the program will exit
        logger.critical(str(datetime.now()) + " --- Connection refused by Elasticsearch server. Please check whether the port number is correct, and the server is up and running.")
        sys.exit(2)
        
    r.encoding = 'utf-8'
    
    #check the HTTP response code returned by Elasticsearch. if it is 200, means no issue. else, display an error message to the user and exit the program
    if r.status_code == 200:
        pass
    elif r.status_code == 401:
        #error 401 means unauthorized
        logger.critical(str(datetime.now()) + " --- Failed to authenticate with Elasticsearch API. Please check your Elasticsearch username and password.")
        sys.exit(2)
    elif r.status_code == 404:
        #error 404 means the ingest pipeline not exists
        logger.critical(str(datetime.now()) + " --- Cloudflare " + ("daily" if daily_pipeline is True else "weekly") + " ingest pipeline is not installed in Elasticsearch. Install first before proceed.")
        sys.exit(1)
    else:
        #other kinds of error may occur and this block of code will handle other errors and display to the user accordingly.
        try:
            response = json.loads(r.text)
            if "error" in response:
                err_type = response["error"]["root_cause"][0]["type"]
                err_msg = response["error"]["root_cause"][0]["reason"]
                logger.critical(str(datetime.now()) + " --- An error occured with error code " + str(r.status_code) + ". Root cause: " + err_type + " | " + err_msg)
                sys.exit(1)
            else:
                logger.critical(str(datetime.now()) + " --- Unknown error occured with error code " + str(r.status_code) + ". Error dump: " + r.text)
                sys.exit(1)
        except json.JSONDecodeError:
            logger.critical(str(datetime.now()) + " --- Unknown error occured with error code " + str(r.status_code) + ". Error dump: " + r.text)
            sys.exit(1)
    

'''
This method is to initialize the folder with the date and time of the logs being stored on local storage as the name of the folder
If the folder does not exists, it will automatically create a new one
'''
def initialize_folder(path_with_date):
    data_folder = Path(path_with_date)
    data_folder.mkdir(parents=True, exist_ok=True)
    return data_folder

'''
This method is to prepare the path of where the logfile will be stored and what will be the name of the logfile.
If the logfile already exists, we assume that the logs has been pulled from Cloudflare previously
'''
def prepare_path(log_start_time_rfc3389, log_end_time_rfc3389, data_folder):
    logfile_name = logfile_name_prefix + "_" + log_start_time_rfc3389 + "~" + log_end_time_rfc3389 + ".json.gz"
    logfile_path = data_folder / logfile_name
    
    if os.path.exists(str(logfile_path)):
        return False
    else:
        return logfile_path
    
'''
A method to check whether the user initiates program exit.
This method will be triggered every time the logpush thread finishes its job (which is, finish the logpush to Elasticsearch process)
This method will minus 1 from the total number of running threads, and check whether the user triggers the program exit process.
If program exit initiated by user, is_exit will become True, and this method will make sure that number of running threads must be zero in order to exit the program gracefully.
'''
def check_if_exited():
    global is_exit, num_of_running_thread
    
    num_of_running_thread -= 1

    if is_exit is True and num_of_running_thread <= 0:
        logger.info(str(datetime.now()) + " --- Program exited gracefully.")
        return True
    
    return False

'''
A method that is responsible for just compressing logs that is written to the local storage, in gzip format
'''
def compress_logs(logfile_path):
    os.system("gzip -f " + str(logfile_path))

'''
This method is responsible to write logs to local storage after the logs have been pulled from Cloudflare API.
After successfully save the logs, it will also trigger compress_logs() method to compress the newly written logs.
'''
def write_logs(log_start_time_rfc3389,  log_end_time_rfc3389, logfile_path, data):
    
    global logger
    
    #open the file as write mode
    logfile = open(logfile_path, mode="w", encoding="utf-8")
    logfile.write(data)
    logfile.close()

    logger.info(str(datetime.now()) + " --- Log range " + log_start_time_rfc3389 + " to " + log_end_time_rfc3389 + ": Logs saved as " + str(logfile_path))

    compress_logs(logfile_path)

    logger.info(str(datetime.now()) + " --- Log range " + log_start_time_rfc3389 + " to " + log_end_time_rfc3389 + ": Logs compressed in gzip format: " + str(logfile_path) + ".gz")
    
'''
This method is to insert a specific line of metadata before each lines of logs, which is required by the Elasticsearch bulk tasks.
It will count the number of lines of logs, and return the final processing result with the number of logs back to the caller
'''
def process_logs(response):
    final_json = ""
    number_of_logs = 0
    
    #this metadata is required by Elasticsearch bulk tasks
    metadata='{ "index": { "_index": "cloudflare" }}'
    
    #feed each lines of logs from the raw logs, split them by newline character
    for line in response.split("\n"):
        if (line == ""):
            #skip empty lines
            pass
        else:
            #first insert the metadata to the final variable, then insert the log
            final_json = final_json + metadata + "\n"
            final_json = final_json + line + "\n"
            number_of_logs += 1
    
    return final_json, number_of_logs

'''
This method will take the processed logs and push them to Elasticsearch, using Bulk API.
'''
def push_logs(final_json, log_start_time_rfc3389, log_end_time_rfc3389, number_of_logs):
    
    global logger
    
    #specify the URL of the Elasticsearch endpoint, and specify the ingest pipeline to be used
    url = "http://localhost:" + port + "/_bulk?pipeline=" + pipeline_name_prefix + ("daily" if daily_pipeline is True else "weekly")
    headers = {"Content-Type": "application/json"}
    auth_elastic = (username, password)
    
    #make a POST request to the Elasticsearch endpoint to push all the logs that is previously processed.
    r = requests.post(url, auth=auth_elastic, headers=headers, data=final_json)
    
    r.encoding = 'utf-8'
    
    #check whether the HTTP response code returned by Elasticsearch endpoint is 200, if yes means the logs have been pushed to Elasticsearch successfully.
    if r.status_code == 200:
        logger.info(str(datetime.now()) + " --- Log range " + log_start_time_rfc3389 + " to " + log_end_time_rfc3389 + ": Successfully pushed " + str(number_of_logs) + " logs to Elasticsearch.")
    else:
        #if the HTTP response code is not 200, means something happened, and an error message will be returned to the user
        try:
            result_json = json.loads(r.text)
            if "error" in result_json:
                err_type = result_json["error"]["root_cause"][0]["type"]
                err_msg = result_json["error"]["root_cause"][0]["reason"]
                logger.error(str(datetime.now()) + " --- Log range " + log_start_time_rfc3389 + " to " + log_end_time_rfc3389 + ": Failed to push logs with error code " + str(r.status_code) + ". Root cause: " + err_type + " | " + err_msg)
            elif "errors" in result_json:
                err_type = result_json["items"][0]["index"]["error"]["type"]
                err_msg = result_json["items"][0]["index"]["error"]["reason"]
                logger.error(str(datetime.now()) + " --- Log range " + log_start_time_rfc3389 + " to " + log_end_time_rfc3389 + ": Failed to push logs with error code " + str(r.status_code) + ". Root cause: " + err_type + " | " + err_msg)
            else:
                logger.error(str(datetime.now()) + " --- Log range " + log_start_time_rfc3389 + " to " + log_end_time_rfc3389 + ": Unexpected error occured with error code " + str(r.status_code) + ". Error dump: " + r.text)
        except json.JSONDecodeError:
            #Elasticsearch should return a JSON object no matter the request is successful or not.
            logger.error(str(datetime.now()) + " --- Log range " + log_start_time_rfc3389 + " to " + log_end_time_rfc3389 + ": Unexpected error occured with error code " + str(r.status_code) + ". Error dump: " + r.text)
    
'''
This method will handle the overall log processing tasks and it will run as a separate thread.
Based on the interval setting configured by the user, this method will only handle logs for a specific time slot.
'''
def logs(current_time, log_start_time_utc, log_end_time_utc):
    
    global path, num_of_running_thread, no_store, logger, retry_attempt
    
    #add one to the variable to indicate number of running threads. useful to determine whether to exit the program gracefully
    num_of_running_thread += 1
    
    #a variable to check whether the request to Cloudflare API is successful.
    request_success = False
    
    #get the current date and hour, these will be used to initialize the folder to store the logs
    today_date = str(current_time.date())
    current_hour = str(current_time.hour) + "00"
    
    #get the log start time and log end time in RFC3389 format, so Cloudflare API will understand it and pull the appropriate logs for us
    log_start_time_rfc3389 = log_start_time_utc.isoformat() + 'Z'
    log_end_time_rfc3389 = log_end_time_utc.isoformat() + 'Z'
    
    #check whether the user wants to store a copy of raw logs on the local storage. if yes, begin the folder initialization process
    if no_store is False:
        
        #initialize the folder with the path specified below
        path_with_date = path + "/" + today_date + "/" + current_hour
        data_folder = initialize_folder(path_with_date)

        #prepare the full path (incl. file name) to store the logs
        logfile_path = prepare_path(log_start_time_rfc3389, log_end_time_rfc3389, data_folder)

        #check the returned value from prepare_path() method. if False, means logfile already exists and no further action required
        if logfile_path is False:

            logger.warning(str(datetime.now()) + " --- Log range " + log_start_time_rfc3389 + " to " + log_end_time_rfc3389 + ": Logfile already exists! Skipping.")

            return check_if_exited()
    
    #specify the URL for the Cloudflare API endpoint, with parameters such as Zone ID, the start time and end time of the logs to pull, timestamp format, sample rate and the fields to be included in the logs
    url = "https://api.cloudflare.com/client/v4/zones/" + zone_id + "/logs/received?start=" + log_start_time_rfc3389 + "&end=" + log_end_time_rfc3389 + "&timestamps="+ timestamp_format +"&sample=" + sample_rate + "&fields=" + fields

    #specify headers for the content type and access token 
    headers = {"Authorization": "Bearer " + access_token, "Content-Type": "application/json"}

    logger.info(str(datetime.now()) + " --- Log range " + log_start_time_rfc3389 + " to " + log_end_time_rfc3389 + ": Requesting logs from Cloudflare...")
    
    #5 retries will be given for the logpull process, in case something happens
    for i in range(retry_attempt):
        #make a GET request to the Cloudflare API
        r = requests.get(url, headers=headers)
        r.encoding = 'utf-8'
        
        #check whether the HTTP response code is 200, if yes then logpull success and exit the loop
        if r.status_code == 200:
            request_success = True
            break
        else:
            #if HTTP response code is not 200, means something happened
            try:
                #load the JSON object to better access the content of it
                response = json.loads(r.text)
            except:
                #something weird happened if the response is not a JSON object, thus print out the error dump
                logger.error(str(datetime.now()) + " --- Log range " + log_start_time_rfc3389 + " to " + log_end_time_rfc3389 + ": Unknown error occured with error code " + str(r.status_code) + ". Error dump: " + r.text + ". " + (("Retrying " + str(i+1) + " of " + retry_attempt + "...") if i < (retry_attempt-1) else ""))
                time.sleep(3)
                continue

            #to check whether "success" key exists in JSON object, if yes, check whether the value is False, and print out the error message
            if "success" in response:
                if response["success"] is False:
                    logger.error(str(datetime.now()) + " --- Log range " + log_start_time_rfc3389 + " to " + log_end_time_rfc3389 + ": Failed to request logs from Cloudflare with error code " + str(response["errors"][0]["code"]) + ": " + response["errors"][0]["message"] + ". " + (("Retrying " + str(i+1) + " of " + retry_attempt + "...") if i < (retry_attempt-1) else ""))
                    time.sleep(3)
                    continue
                else:
                    #something weird happened if it is not False. If the request has been successfully done, it should not return this kind of error, instead the raw logs should be returned with HTTP response code 200.
                    logger.error(str(datetime.now()) + " --- Log range " + log_start_time_rfc3389 + " to " + log_end_time_rfc3389 + ": Unknown error occured with error code " + str(r.status_code) + ". Error dump: " + r.text + ". " + (("Retrying " + str(i+1) + " of " + retry_attempt + "...") if i < (retry_attempt-1) else ""))
                    time.sleep(3)
                    continue
            else:
                #other type of error may occur, which may not return a JSON object.
                logger.error(str(datetime.now()) + " --- Log range " + log_start_time_rfc3389 + " to " + log_end_time_rfc3389 + ": Unknown error occured with error code " + str(r.status_code) + ". Error dump: " + r.text + ". " + (("Retrying " + str(i+1) + " of " + retry_attempt + "...") if i < (retry_attempt-1) else ""))
                time.sleep(3)
                continue
            
    #check whether the logpull process from Cloudflare API has been successfully completed, if yes then proceed with next steps
    if request_success is True:

        #check whether the user wants to store a copy of raw logs on the local storage. if not, skip the process and proceed with logpush process
        if no_store is False:
            logger.info(str(datetime.now()) + " --- Log range " + log_start_time_rfc3389 + " to " + log_end_time_rfc3389 + ": Logs requested. Saving logs...")
            write_logs(log_start_time_rfc3389,  log_end_time_rfc3389, logfile_path, r.text)
        else:
            logger.info(str(datetime.now()) + " --- Log range " + log_start_time_rfc3389 + " to " + log_end_time_rfc3389 + ": Logs requested. Raw logs will not be saved on local storage.")

        logger.info(str(datetime.now()) + " --- Log range " + log_start_time_rfc3389 + " to " + log_end_time_rfc3389 + ": Processing logs for Elasticsearch Bulk tasks.")

        #invoke process_logs method to make the logs compatible with Elasticsearch bulk tasks. 
        #this method will return the final result with the number of logs processed
        final_json, number_of_logs = process_logs(r.text)
        
        #check whether the number of logs processed is less than or equal to zero. if yes means that the logpush process is no longer required, thus skip the process
        if number_of_logs <= 0:
            
            logger.info(str(datetime.now()) + " --- Log range " + log_start_time_rfc3389 + " to " + log_end_time_rfc3389 + ": 0 logs requested from this log range. No further action required.")
            
            #invoke this method to check whether the user triggers program exit sequence, and ends the thread
            return check_if_exited()

        logger.info(str(datetime.now()) + " --- Log range " + log_start_time_rfc3389 + " to " + log_end_time_rfc3389 + ": " + str(number_of_logs) + " logs processed.")

        logger.info(str(datetime.now()) + " --- Log range " + log_start_time_rfc3389 + " to " + log_end_time_rfc3389 + ": Pushing " + str(number_of_logs) + " logs to Elasticsearch...")

        #finally, push logs to Elasticsearch
        push_logs(final_json, log_start_time_rfc3389, log_end_time_rfc3389, number_of_logs)

    #invoke this method to check whether the user triggers program exit sequence
    return check_if_exited()

        
####################################################################################################       
        
        
#This is where the real execution of the program begins. First it will initialize the parameters supplied by the user
initialize_arg()

#After the above execution, it will verify the Zone ID and Access Token given by the user whether they are valid
verify_credential()

#if both Zone ID and Access Token are valid, the logpush tasks to Elastic will begin.
logger.info(str(datetime.now()) + " --- Cloudflare log push tasks to Elastic started.")


#first get the current system time, both local and UTC time.
#the purpose of getting UTC time is to facilitate the calculation of the start and end time to pull the logs from Cloudflare API
#the purpose of getting local time is to generate a directory structure to store logs, separated by the date and time
current_time_utc = datetime.utcnow()
current_time = datetime.now()

#calculate how many seconds to go back from current time to pull the logs. 
logs_from = 60.0 + ((interval // 60 * 60) + 60)

#calculate the start time to pull the logs from Cloudflare API
log_start_time_utc = current_time_utc.replace(second=0, microsecond=0) - timedelta(seconds=logs_from)
current_time = current_time.replace(second=0, microsecond=0) - timedelta(seconds=logs_from)

#this is useful when we need to repeat the execution of a code block after a certain interval, in an accurate way
#below code will explain the usage of this in detail
initial_time = time.time()

#force the program to run indefinitely, unless the user stops it with Ctrl+C
while True:
    
    #calculate the end time to pull the logs from Cloudflare API, based on the interval value given by the user
    log_end_time_utc = log_start_time_utc + timedelta(seconds=interval)

    #create a new thread to handle the logs processing. the target method is logs() and 3 parameters are supplied to this method
    threading.Thread(target=logs, args=(current_time, log_start_time_utc, log_end_time_utc)).start()

    log_start_time_utc = log_end_time_utc
    current_time = current_time + timedelta(seconds=interval)

    try:
        time.sleep(interval - ((time.time() - initial_time) % interval))
    except KeyboardInterrupt:
        is_exit = True
        print("")
        logger.info(str(datetime.now()) + " --- Initiating program exit. Finishing up log push tasks...")
        if num_of_running_thread <= 0:
            logger.info(str(datetime.now()) + " --- Program exited gracefully.")
        break
        