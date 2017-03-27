import requests
import configobj
import os
import sys
import socket
import logging
import logging.handlers
import backoff
import json
import yagmail
import schedule
import time
import daemon

# ensure we correctly handle all keyboard interrupts
import signal
signal.signal(signal.SIGINT, signal.default_int_handler)

# required to suppress ssl warning for urllib3 (requests uses urllib3)
import requests.packages.urllib3
requests.packages.urllib3.disable_warnings()

dht_root_dir = os.path.dirname(os.path.realpath(__file__)).decode("utf-8")

# set folder path for config files
config_dir = os.path.join(dht_root_dir, u"configs")
config_dir = os.path.normpath(config_dir)

# set path for configspec.ini file
configspec_ini = os.path.join(config_dir, u"configspec.ini")

# set path for config.ini file
config_ini = os.path.join(config_dir, u"config.ini")

# set folder path for log files
logs_dir = os.path.join(dht_root_dir, u"logs")
logs_dir = os.path.normpath(logs_dir)

# set path for log file
app_log_file = os.path.join(logs_dir, u"app.log")

# create configobj instance, set config.ini file, set encoding and set configspec.ini file
config_obj = configobj.ConfigObj(config_ini, list_values=False, write_empty_values=True, encoding='UTF-8',
                                default_encoding='UTF-8', configspec=configspec_ini, unrepr=True)

user_agent_chrome = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/49.0.2623.112 Safari/537.36"


def app_logging():

    # read log levels
    log_level = config_obj["general"]["log_level"]

    # setup formatting for log messages
    app_formatter = logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(module)s %(funcName)s :: %(message)s")

    # setup logger for app
    app_logger = logging.getLogger("app")

    # add rotating log handler
    app_rotatingfilehandler = logging.handlers.RotatingFileHandler(app_log_file, "a", maxBytes=10485760, backupCount=3, encoding="utf-8")

    # set formatter for app
    app_rotatingfilehandler.setFormatter(app_formatter)

    # add the log message handler to the logger
    app_logger.addHandler(app_rotatingfilehandler)

    # set level of logging from config
    if log_level == "INFO":

        app_logger.setLevel(logging.INFO)

    elif log_level == "WARNING":

        app_logger.setLevel(logging.WARNING)

    elif log_level == "exception":

        app_logger.setLevel(logging.ERROR)

    # setup logging to console
    console_streamhandler = logging.StreamHandler()

    # set formatter for console
    console_streamhandler.setFormatter(app_formatter)

    # add handler for formatter to the console
    app_logger.addHandler(console_streamhandler)

    # set level of logging from config
    if log_level == "INFO":

        console_streamhandler.setLevel(logging.INFO)

    elif log_level == "WARNING":

        console_streamhandler.setLevel(logging.WARNING)

    elif log_level == "exception":

        console_streamhandler.setLevel(logging.ERROR)

    return app_logger




def notification_email(app_name, repo_name, target_repo_name, previous_version, current_version):

    # read email config
    config_email_username = config_obj["notification"]["email_username"]
    config_email_password = config_obj["notification"]["email_password"]
    config_email_to = config_obj["notification"]["email_to"]

    # construct url to docker hub build details
    config_target_repo_owner = config_obj["general"]["target_repo_owner"]
    dockerhub_build_details = "https://hub.docker.com/r/%s/%s/builds/" % (config_target_repo_owner, target_repo_name)

    app_log.info(u'Sending email notification...')

    yag = yagmail.SMTP(config_email_username, config_email_password)
    subject = 'Trigger Build: [%s] - version changed from %s to %s' % (app_name, previous_version, current_version)
    html = '''
    <b>Status:</b> Release triggered<br>
    <b>Application:</b> %s<br>
    <b>Repository:</b> %s<br>
    <b>Previous Version:</b> %s<br>
    <b>Current Version:</b> %s<br>
    <b>Docker Hub:</b> <a href="%s">Build Details</a><br>
    ''' % (app_name, repo_name, previous_version, current_version, dockerhub_build_details)
    yag.send(to = config_email_to, subject = subject, contents = [html])


@backoff.on_exception(backoff.expo, (socket.timeout, requests.exceptions.Timeout, requests.exceptions.HTTPError),
                      max_tries=10)
def http_client(**kwargs):

    if kwargs is not None:

        if "url" in kwargs:

            url = kwargs['url']

        else:

            app_log.warning(u'No URL sent to function, exiting function...')
            return 1, None

        if "user_agent" in kwargs:

            user_agent = kwargs['user_agent']

        else:

            app_log.warning(u'No User Agent sent to function, exiting function...')
            return 1, None

        if "request_type" in kwargs:

            request_type = kwargs['request_type']

        else:

            app_log.warning(u'No request type (get/put/post) sent to function, exiting function...')
            return 1, None

        # optional stuff to include
        if "auth" in kwargs:

            auth = kwargs['auth']

        else:

            auth = None

        if "additional_header" in kwargs:

            additional_header = kwargs['additional_header']

        else:

            additional_header = None

        if "data_payload" in kwargs:

            data_payload = kwargs['data_payload']

        else:

            data_payload = None

    else:

        app_log.warning(u'No keyword args sent to function, exiting function...')
        return 1

    # add headers for gzip support and custom user agent string

    # set connection timeout value (max time to wait for connection)
    connect_timeout = 10.0

    # set read timeout value (max time to wait between each byte)
    read_timeout = 5.0

    # use a session instance to customize how "requests" handles making http requests
    session = requests.Session()

    # set status_code and content to None in case nothing returned
    content = None

    try:

        # define dict of common arguments for requests
        requests_data_dict = {'url': url, 'timeout': (connect_timeout, read_timeout), 'allow_redirects': True, 'verify': False}

        session.headers = {
            'Accept-encoding': 'gzip',
            'User-Agent': user_agent
        }

        if "additional_header" in kwargs:

            # append to headers dict with additional headers dict
            session.headers.update(additional_header)

        if "auth" in kwargs:

            session.auth = auth

        if request_type == "put":

            # add additional keyword arguments
            requests_data_dict.update({'data': data_payload})

        elif request_type == "post":

            # add additional keyword arguments
            requests_data_dict.update({'data': data_payload})

        # construct class.method from request_type
        request_method = getattr(session, request_type)

        # use keyword argument unpack to convert dict to keyword args
        response = request_method(**requests_data_dict)

        # get status code and content returned
        status_code = response.status_code
        content = response.content

        if status_code == 401:

            app_log.warning(u"The status code %s indicates the github personal token is revoked %s, error is %s" % (status_code, url, content))
            raise requests.exceptions.HTTPError

        elif status_code == 404:

            app_log.warning(u"The status code %s indicates the remote site is down for %s, error is %s" % (status_code, url, content))
            raise requests.exceptions.HTTPError

        elif status_code == 422:

            app_log.warning(u"The status code %s indicates a duplicate release already exists on github for %s, error is %s" % (status_code, url, content))
            raise requests.exceptions.HTTPError

        elif status_code != 200 and status_code != 201:

            app_log.warning(u"The status code %s indicates an unexpected error for %s, error is %s" % (status_code, url, content))
            raise requests.exceptions.HTTPError

    except requests.exceptions.ConnectTimeout as content:

        # connect timeout occurred
        app_log.warning(u"Connection timed for URL %s with error %s" % (url, content))
        return 1, content

    except requests.exceptions.ConnectionError as content:

        # connection error occurred
        app_log.warning(u"Connection error for URL %s with error %s" % (url, content))
        return 1, content

    except requests.exceptions.TooManyRedirects as content:

        # too many redirects, bad site or circular redirect
        app_log.warning(u"Too many retries for URL %s with error %s" % (url, content))
        return 1, content

    except requests.exceptions.HTTPError:

        # catch http exceptions thrown by requests
        return 1, content

    except requests.exceptions.RequestException as content:

        # catch any other exceptions thrown by requests
        app_log.warning(u"Caught other exceptions for URL %s with error %s" % (url, content))
        return 1, content

    else:

        # if status code is not 200 and not 404 (file not found) then raise exception to cause backoff
        if status_code == 200:

            app_log.info(u"The status code %s indicates a successful operation for %s" % (status_code, url))
            return 0, content

        # if status code is not 200 or 201 and not 404 (file not found) then raise exception to cause backoff
        elif status_code == 201:

            app_log.info(u"The status code %s indicates a successful creation of a github release for %s" % (status_code, url))
            return 0, content


def github_create_release(current_version, target_repo_owner, target_repo_name, target_access_token):

    app_log.info(u"Creating Release on GitHub for version %s..." % current_version)

    target_repo_owner = target_repo_owner
    target_repo_name = target_repo_name
    target_access_token = target_access_token
    github_tag_name = "%s-01" % current_version
    github_release_name = "API/URL triggered release"
    github_release_body = github_tag_name
    request_type = "post"
    http_url = 'https://api.github.com/repos/%s/%s/releases?access_token=%s' % (target_repo_owner, target_repo_name, target_access_token)
    data_payload = '{"tag_name": "%s","target_commitish": "master","name": "%s","body": "%s","draft": false,"prerelease": false}' % (github_tag_name, github_release_name, github_release_body)

    # process post request
    status_code, content = http_client(url=http_url, user_agent=user_agent_chrome, request_type=request_type, data_payload=data_payload)
    return status_code, content


def monitor_sites(schedule_check_mins):

    # read sites list from config
    config_site_list = config_obj["monitor_sites"]["site_list"]
    target_repo_owner = config_obj["general"]["target_repo_owner"]
    target_access_token = config_obj["general"]["target_access_token"]

    # loop over each site and check previous and current result
    for site_item in config_site_list:

        source_site_name = site_item["source_site_name"]
        source_app_name = site_item["source_app_name"]
        source_repo_name = site_item["source_repo_name"]
        target_repo_name = site_item["target_repo_name"]

        app_log.info(u"-------------------------------------")
        app_log.info(u"Processing started for application %s..." % source_app_name)

        if source_site_name == "github":

            # use github rest api to get app release info
            url = "https://api.github.com/repos/%s/%s/tags" % (source_repo_name, source_app_name)
            request_type = "get"

            # download webpage content
            status_code, content = http_client(url=url, auth=('binhex', target_access_token), user_agent=user_agent_chrome, request_type=request_type)

            if status_code == 0:

                content = json.loads(content)

            else:

                app_log.info(u"[ERROR] Problem downloading app info, skipping to new release...")
                continue

            try:

                # get tag name from json
                current_version = content[0]['name']

            except IndexError:

                current_version = "0.0"

        elif source_site_name == "aor":

            # use aor unofficial api to get app release info
            url = "https://www.archlinux.org/packages/search/json/?q=%s&arch=any&arch=x86_64" % source_app_name
            request_type = "get"

            # download webpage content
            status_code, content = http_client(url=url, user_agent=user_agent_chrome, request_type=request_type)

            if status_code == 0:

                content = json.loads(content)

            else:

                app_log.info(u"[ERROR] Problem downloading app info, skipping to new release...")
                continue

            try:

                # get package version and release number from json
                pkgver = content['results'][0]['pkgver']
                pkgrel = content['results'][0]['pkgrel']

                # construct app version
                current_version = "%s-%s" % (pkgver, pkgrel)

                last_update = content['results'][0]['last_update']

            except IndexError:

                current_version = "0.0"

        elif source_site_name == "aur":

            # use aur api to get app release info
            url = "https://aur.archlinux.org/rpc/?v=5&type=info&arg[]=%s" % source_app_name
            request_type = "get"

            # download webpage content
            status_code, content = http_client(url=url, user_agent=user_agent_chrome, request_type=request_type)

            if status_code == 0:

                content = json.loads(content)

            else:

                app_log.info(u"[ERROR] Problem downloading app info, skipping to new release...")
                continue

            try:

                # get app version from json
                current_version = content["results"][0]["Version"]

            except IndexError:

                current_version = "0.0"

        # write value for current match to config
        config_obj["results"]["%s_%s_current_version" % (source_site_name, source_app_name)] = current_version
        config_obj.write()

        try:

            # read value from previous match from config
            previous_version = config_obj["results"]["%s_%s_previous_version" % (source_site_name, source_app_name)]

        except KeyError:

            app_log.info(u"No known previous version for app %s, assuming first run" % source_app_name)
            app_log.info(u"Setting previous version to current version %s and going to next iteration" % current_version)
            config_obj["results"]["%s_%s_previous_version" % (source_site_name, source_app_name)] = current_version
            config_obj.write()
            continue

        if previous_version != current_version:

            app_log.info(u"[TRIGGER] Previous version %s and current version %s are different, triggering a docker hub build (via github tag)..." % (previous_version, current_version))
            status_code, content = github_create_release(current_version, target_repo_owner, target_repo_name, target_access_token)

            if status_code == 0:

                app_log.info(u"Setting previous version %s to the same as current version %s after successful build" % (previous_version, current_version))
                config_obj["results"]["%s_%s_previous_version" % (source_site_name, source_app_name)] = current_version
                config_obj.write()

                # send email notification
                notification_email(source_app_name, source_repo_name, target_repo_name, previous_version, current_version)

            else:

                app_log.warning(u"[ERROR] Problem creating github release and tag, skipping to next iteration...")
                continue

        else:

            app_log.info(u"[SKIPPED] Previous version %s and current version %s match, nothing to do" % (previous_version, current_version))

        app_log.info(u"Processing finished for application %s" % source_app_name)

    app_log.info(u"All applications processed, waiting for next invocation in %s minutes..." % schedule_check_mins)

# required to prevent separate process from trying to load parent process
if __name__ == '__main__':

    with daemon.DaemonContext():

        app_log = app_logging()

        app_log.info(u"Monitoring sites for application version changes...")

        schedule_check_mins = config_obj["general"]["schedule_check_mins"]
        app_log.info(u"Checking for changes every %s minutes..." % schedule_check_mins)

        schedule.every(schedule_check_mins).minutes.do(monitor_sites, schedule_check_mins)

        while True:

            try:

                schedule.run_pending()
                time.sleep(1)

            except KeyboardInterrupt:

                app_log.info(u"Keyboard interrupt received, exiting script...")
                sys.exit()
