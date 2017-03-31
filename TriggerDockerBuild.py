import requests
import configobj
import argparse
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
import requests.packages.urllib3
import signal
requests.packages.urllib3.disable_warnings() # required to suppress ssl warning for urllib3 (requests uses urllib3)
signal.signal(signal.SIGINT, signal.default_int_handler) # ensure we correctly handle all keyboard interrupts

# TODO change input to functions as dictionary
# TODO change functions to **kwargs and use .get() to get value (will be none if not fund)
# TODO change return for function to dictionary

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
config_obj = configobj.ConfigObj(config_ini, list_values=False, write_empty_values=True, encoding='UTF-8', default_encoding='UTF-8', configspec=configspec_ini, unrepr=True)

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

    return {'logger':app_logger, 'handler':app_rotatingfilehandler}


def notification_email(action, source_app_name, source_repo_name, source_site_name, source_site_url, target_repo_name, previous_version, current_version):

    # read email config
    config_email_username = config_obj["notification"]["email_username"]
    config_email_password = config_obj["notification"]["email_password"]
    config_email_to = config_obj["notification"]["email_to"]

    # construct url to docker hub build details
    target_repo_owner = config_obj["general"]["target_repo_owner"]
    dockerhub_build_details = "https://hub.docker.com/r/%s/%s/builds/" % (target_repo_owner, target_repo_name)

    app_logger_instance.info(u'Sending email notification...')

    yag = yagmail.SMTP(config_email_username, config_email_password)
    subject = '[%s] %s - version changed from %s to %s' % (source_app_name, action, previous_version, current_version)
    html = '''
    <b>Action:</b> %s<br>
    <b>Previous Version:</b> %s<br>
    <b>Current Version:</b> %s<br>
    <b>Source Site Name:</b> %s<br>
    <b>Source Repository:</b> %s<br>
    <b>Source App Name:</b> %s<br>
    <b>Source Site URL:</b> %s<br>
    <b>Target Repository:</b> %s<br>
    ''' % (action, previous_version, current_version, source_site_name, source_repo_name, source_app_name, source_site_url, target_repo_name)

    if action == "trigger":

        html += '''<b>Target Build URL:</b> %s<br>''' % dockerhub_build_details

    yag.send(to = config_email_to, subject = subject, contents = [html])


@backoff.on_exception(backoff.expo, (socket.timeout, requests.exceptions.Timeout, requests.exceptions.HTTPError),
                      max_tries=10)
def http_client(**kwargs):

    if kwargs is not None:

        if "url" in kwargs:

            url = kwargs['url']

        else:

            app_logger_instance.warning(u'No URL sent to function, exiting function...')
            return 1, None

        if "user_agent" in kwargs:

            user_agent = kwargs['user_agent']

        else:

            app_logger_instance.warning(u'No User Agent sent to function, exiting function...')
            return 1, None

        if "request_type" in kwargs:

            request_type = kwargs['request_type']

        else:

            app_logger_instance.warning(u'No request type (get/put/post) sent to function, exiting function...')
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

        app_logger_instance.warning(u'No keyword args sent to function, exiting function...')
        return 1

    # add headers for gzip support and custom user agent string

    # set connection timeout value (max time to wait for connection)
    connect_timeout = 10.0

    # set read timeout value (max time to wait between each byte)
    read_timeout = 5.0

    # use a session instance to customize how "requests" handles making http requests
    session = requests.Session()

    # set status_code and content to None in case nothing returned
    status_code = None
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

            app_logger_instance.warning(u"The status code %s indicates unauthorised access for %s, error is %s" % (status_code, url, content))
            raise requests.exceptions.HTTPError

        elif status_code == 404:

            app_logger_instance.warning(u"The status code %s indicates the requested resource could not be found  for %s, error is %s" % (status_code, url, content))
            raise requests.exceptions.HTTPError

        elif status_code == 422:

            app_logger_instance.warning(u"The status code %s indicates a request was well-formed but was unable to be followed due to semantic errors for %s, error is %s" % (status_code, url, content))
            raise requests.exceptions.HTTPError

        elif not 200 <= status_code <= 299:

            app_logger_instance.warning(u"The status code %s indicates an unexpected error for %s, error is %s" % (status_code, url, content))
            raise requests.exceptions.HTTPError

    except requests.exceptions.ConnectTimeout as content:

        # connect timeout occurred
        app_logger_instance.warning(u"Connection timed for URL %s with error %s" % (url, content))
        return 1, status_code, content

    except requests.exceptions.ConnectionError as content:

        # connection error occurred
        app_logger_instance.warning(u"Connection error for URL %s with error %s" % (url, content))
        return 1, status_code, content

    except requests.exceptions.TooManyRedirects as content:

        # too many redirects, bad site or circular redirect
        app_logger_instance.warning(u"Too many retries for URL %s with error %s" % (url, content))
        return 1, status_code, content

    except requests.exceptions.HTTPError:

        # catch http exceptions thrown by requests
        return 1, status_code, content

    except requests.exceptions.RequestException as content:

        # catch any other exceptions thrown by requests
        app_logger_instance.warning(u"Caught other exceptions for URL %s with error %s" % (url, content))
        return 1, status_code, content

    else:

        if 200 <= status_code <= 299:

            app_logger_instance.info(u"The status code %s indicates a successful request for %s" % (status_code, url))
            return 0, status_code, content


def github_create_release(current_version, target_repo_owner, target_repo_name, target_access_token):

    app_logger_instance.info(u"Creating Release on GitHub for version %s..." % current_version)

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
    return_code, status_code, content = http_client(url=http_url, user_agent=user_agent_chrome, request_type=request_type, data_payload=data_payload)
    return return_code, status_code, content


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
        action = site_item["action"]

        app_logger_instance.info(u"-------------------------------------")
        app_logger_instance.info(u"Processing started for application %s..." % source_app_name)

        if source_site_name == "github":

            # use github rest api to get app release info
            url = "https://api.github.com/repos/%s/%s/tags" % (source_repo_name, source_app_name)
            request_type = "get"

            # download webpage content
            return_code, status_code, content = http_client(url=url, auth=('binhex', target_access_token), user_agent=user_agent_chrome, request_type=request_type)

            if return_code == 0:

                content = json.loads(content)

            else:

                app_logger_instance.info(u"[ERROR] Problem downloading json content from %s, skipping to new release..." % url)
                continue

            try:

                # get tag name from json
                current_version = content[0]['name']

            except IndexError:

                app_logger_instance.info(u"[ERROR] Problem parsing json from %s, skipping to next iteration..." % url)
                continue

            source_site_url = "https://github.com/%s/%s/releases" % (source_repo_name, source_app_name)

        elif source_site_name == "aor":

            # use aor unofficial api to get app release info
            url = "https://www.archlinux.org/packages/search/json/?q=%s&arch=any&arch=x86_64" % source_app_name
            request_type = "get"

            # download webpage content
            return_code, status_code, content = http_client(url=url, user_agent=user_agent_chrome, request_type=request_type)

            if return_code == 0:

                content = json.loads(content)

            else:

                app_logger_instance.info(u"[ERROR] Problem downloading json content from %s, skipping to new release..." % url)
                continue

            try:

                # get package version and release number from json
                pkgver = content['results'][0]['pkgver']
                pkgrel = content['results'][0]['pkgrel']

                # construct app version
                current_version = "%s-%s" % (pkgver, pkgrel)

                # get repo name and arch type (used in url construct for email notification)
                source_repo_name = content['results'][0]['repo']
                source_arch_name = content['results'][0]['arch']

            except IndexError:

                app_logger_instance.info(u"[ERROR] Problem parsing json from %s, skipping to next iteration..." % url)
                continue

            source_site_url = "https://www.archlinux.org/packages/%s/%s/%s/" % (source_repo_name, source_arch_name, source_app_name)

        elif source_site_name == "aur":

            # use aur api to get app release info
            url = "https://aur.archlinux.org/rpc/?v=5&type=info&arg[]=%s" % source_app_name
            request_type = "get"

            # download webpage content
            return_code, status_code, content = http_client(url=url, user_agent=user_agent_chrome, request_type=request_type)

            if return_code == 0:

                content = json.loads(content)

            else:

                app_logger_instance.info(u"[ERROR] Problem downloading json content from %s, skipping to new release..." % url)
                continue

            try:

                # get app version from json
                current_version = content["results"][0]["Version"]

            except IndexError:

                app_logger_instance.info(u"[ERROR] Problem parsing json from %s, skipping to next iteration..." % url)
                continue

            source_site_url = "https://aur.archlinux.org/packages/%s/" % source_app_name

        else:

            app_logger_instance.info(u"[ERROR] Source site name %s unknown, skipping to next iteration..." % source_site_name)
            continue

        # write value for current match to config
        config_obj["results"]["%s_%s_%s_current_version" % (source_site_name, source_app_name, target_repo_name)] = current_version
        config_obj.write()

        try:

            # read value from previous match from config
            previous_version = config_obj["results"]["%s_%s_%s_previous_version" % (source_site_name, source_app_name, target_repo_name)]

        except KeyError:

            app_logger_instance.info(u"No known previous version for app %s, assuming first run" % source_app_name)
            app_logger_instance.info(u"Setting previous version to current version %s and going to next iteration" % current_version)
            config_obj["results"]["%s_%s_%s_previous_version" % (source_site_name, source_app_name, target_repo_name)] = current_version
            config_obj.write()
            continue

        if previous_version != current_version:

            app_logger_instance.info(u"[TRIGGER] Previous version %s and current version %s are different, triggering a docker hub build (via github tag)..." % (previous_version, current_version))
            return_code, status_code, content = github_create_release(current_version, target_repo_owner, target_repo_name, target_access_token)

            if status_code == 201:

                app_logger_instance.info(u"Setting previous version %s to the same as current version %s after successful build" % (previous_version, current_version))
                config_obj["results"]["%s_%s_%s_previous_version" % (source_site_name, source_app_name, target_repo_name)] = current_version
                config_obj.write()

                # send email notification
                notification_email(action, source_app_name, source_repo_name, source_site_name, source_site_url, target_repo_name, previous_version, current_version)

            elif status_code == 422:

                app_logger_instance.warning(u"[ERROR] github release already exists for %s/%s, skipping build" % (target_repo_owner, target_repo_name))
                config_obj["results"]["%s_%s_%s_previous_version" % (source_site_name, source_app_name, target_repo_name)] = current_version
                config_obj.write()

            else:

                app_logger_instance.warning(u"[ERROR] Problem creating github release and tag, skipping to next iteration...")
                continue

        else:

            app_logger_instance.info(u"[SKIPPED] Previous version %s and current version %s match, nothing to do" % (previous_version, current_version))

        app_logger_instance.info(u"Processing finished for application %s" % source_app_name)

    app_logger_instance.info(u"All applications processed, waiting for next invocation in %s minutes..." % schedule_check_mins)


def start():

    schedule_check_mins = config_obj["general"]["schedule_check_mins"]

    app_logger_instance.info(u"Initial check for version changes...")
    monitor_sites(schedule_check_mins)

    # now run monitor_sites function via scheduler
    schedule.every(schedule_check_mins).minutes.do(monitor_sites, schedule_check_mins)

    while True:

        try:

            schedule.run_pending()
            time.sleep(1)

        except KeyboardInterrupt:

            app_logger_instance.info(u"Keyboard interrupt received, exiting script...")
            sys.exit()

# required to prevent separate process from trying to load parent process
if __name__ == '__main__':

    version = "1.0.0"
    app_log = app_logging()
    app_logger_instance = app_log.get('logger')
    app_handler = app_log.get('handler')

    # custom argparse to redirect user to help if unknown argument specified
    class ArgparseCustom(argparse.ArgumentParser):

        def error(self, message):
            sys.stderr.write('error: %s\n' % message)
            self.print_help()
            sys.exit(2)

    # setup argparse description and usage, also increase spacing for help to 50
    commandline_parser = ArgparseCustom(prog="TriggerDockerBuild", description="%(prog)s " + version, usage="%(prog)s [--help] [--config <path>] [--logs <path>] [--pidfile <path>] [--daemon] [--version]", formatter_class=lambda prog: argparse.HelpFormatter(prog, max_help_position=50))

    # add argparse command line flags
    commandline_parser.add_argument(u"--config", metavar=u"<path>", help=u"specify path for config file e.g. --config /opt/triggerdockerbuild/config/")
    commandline_parser.add_argument(u"--logs", metavar=u"<path>", help=u"specify path for log files e.g. --logs /opt/triggerdockerbuild/logs/")
    commandline_parser.add_argument(u"--pidfile", metavar=u"<path>", help=u"specify path to pidfile e.g. --pid /var/run/triggerdockerbuild/triggerdockerbuild.pid")
    commandline_parser.add_argument(u"--daemon", action=u"store_true", help=u"run as daemonized process")
    commandline_parser.add_argument(u"--version", action=u"version", version=version)

    # save arguments in dictionary
    args = vars(commandline_parser.parse_args())

    # check os is not windows and then run main process as daemonized process
    if args["daemon"] is True and os.name != "nt":

        app_logger_instance.info(u"Running as a daemonized process...")

        # specify the logging handler as an exclusion to the daemon, to prevent its output being closed
        daemon_context = daemon.DaemonContext()
        daemon_context.files_preserve = [app_handler.stream]
        daemon_context.open()

        schedule_check_mins = config_obj["general"]["schedule_check_mins"]

        app_logger_instance.info(u"Initial check for version changes...")
        monitor_sites(schedule_check_mins)

        # now run monitor_sites function via scheduler
        schedule.every(schedule_check_mins).minutes.do(monitor_sites, schedule_check_mins)

        while True:

            try:

                schedule.run_pending()
                time.sleep(1)

            except KeyboardInterrupt:

                app_logger_instance.info(u"Keyboard interrupt received, exiting script...")
                sys.exit()

    else:

            app_logger_instance.info(u"Running as a foreground process...")
            start()
