import requests
import configobj
import os
import socket
import logging
import logging.handlers
import backoff
import json
import yagmail

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

app_log = app_logging()


def notification_email(app_name, repo_name):

    # read email config
    config_email_username = config_obj["notification"]["email_username"]
    config_email_password = config_obj["notification"]["email_password"]
    config_email_to = config_obj["notification"]["email_to"]

    # construct url to docker hub build details
    config_github_owner = config_obj["general"]["github_owner"]
    dockerhub_build_details = "https://hub.docker.com/r/%s/arch-%s/builds/" % (config_github_owner, app_name)

    app_log.info(u'Sending email notification...')

    yag = yagmail.SMTP(config_email_username, config_email_password)
    subject = 'API/URL triggered build'
    body = 'Release triggered for app %s at repo %s' % (app_name, repo_name)
    html = 'Link to Docker Hub build page %s' % dockerhub_build_details
    yag.send(to = config_email_to, subject = subject, contents = [body, html])


@backoff.on_exception(backoff.expo, (socket.timeout, requests.exceptions.Timeout, requests.exceptions.HTTPError),
                      max_tries=10)
def http_client(**kwargs):

    if kwargs is not None:

        if "url" in kwargs:

            url = kwargs['url']

        else:

            app_log.warning(u'No URL sent to function, exiting function...')
            return 1

        if "user_agent" in kwargs:
            user_agent = kwargs['user_agent']

        else:

            app_log.warning(u'No Usent Agent sent to function, exiting function...')
            return 1

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
    headers = {
        'Accept-encoding': 'gzip',
        'User-Agent': user_agent
    }

    if "additional_header" in kwargs:

        # add any additional headers
        headers.update(additional_header)

    # set connection timeout value (max time to wait for connection)
    connect_timeout = 10.0

    # set read timeout value (max time to wait between each byte)
    read_timeout = 5.0

    # use a session instance to customize how "requests" handles making http requests
    session = requests.Session()

    # set status_code and content to None in case nothing returned
    content = None

    try:

        if "data_payload" in kwargs:

            # request url get with timeouts and custom headers
            response = session.post(url=url, timeout=(connect_timeout, read_timeout), headers=headers, allow_redirects=True, verify=False, data=data_payload)

        else:

            # request url get with timeouts and custom headers
            response = session.get(url=url, timeout=(connect_timeout, read_timeout), headers=headers, allow_redirects=True,
                                   verify=False)

        # get status code and content downloaded
        status_code = response.status_code
        content = response.content

        # if status code is not 200 and not 404 (file not found) then raise exception to cause backoff
        if status_code == 200:

            app_log.info(u"The status code %s indicates a successful operation for %s" % (status_code, url))
            return 0, content

        # if status code is not 200 or 201 and not 404 (file not found) then raise exception to cause backoff
        elif status_code == 201:

            app_log.info(u"The status code %s indicates a successful creation of a github release for %s" % (status_code, url))
            return 0, content

        elif status_code == 404:

            app_log.warning(u"The status code %s indicates the remote site is down for %s" % (status_code, url))
            raise requests.exceptions.HTTPError

        elif status_code == 422:

            app_log.warning(u"The status code %s indicates a duplicate release already exists on github for %s" % (status_code, url))
            raise requests.exceptions.HTTPError

    except requests.exceptions.ConnectTimeout:

        # connect timeout occurred
        app_log.warning(u"Index site feed/api download connect timeout for %s" % url)
        return 1, content

    except requests.exceptions.ConnectionError as e:

        # connection error occurred
        app_log.warning(u"Index site feed/api download connection error %s for %s" % (e, url))
        return 1, content

    except requests.exceptions.TooManyRedirects:

        # too many redirects, bad site or circular redirect
        app_log.warning(u"Index site feed/api download too many redirects for %s" % url)
        return 1, content

    except requests.exceptions.HTTPError:

        # catch http exceptions thrown by requests
        return 1, content

    except requests.exceptions.RequestException:

        # catch any other exceptions thrown by requests
        app_log.warning(u"Index site feed/api download failed, giving up for %s" % url)
        return 1, content


def github_create_release(current_version, app_name):

    config_github_owner = config_obj["general"]["github_owner"]
    config_github_access_token = config_obj["general"]["github_access_token"]

    app_log.info(u"Creating Release on GitHub for version %s..." % current_version)

    github_owner = config_github_owner
    github_repo = "arch-%s" % app_name
    github_access_token = config_github_access_token
    github_tag_name = "%s-01" % current_version
    github_release_name = "API/URL triggered release"
    github_release_body = github_tag_name
    http_request_type = "post"
    http_url = 'https://api.github.com/repos/%s/%s/releases?access_token=%s' % (github_owner, github_repo, github_access_token)
    http_data_payload = '{"tag_name": "%s","target_commitish": "master","name": "%s","body": "%s","draft": false,"prerelease": false}' % (github_tag_name, github_release_name, github_release_body)

    # process post request
    status_code, content = http_client(url=http_url, user_agent=user_agent_chrome, request_type=http_request_type, data_payload=http_data_payload)
    return status_code, content


def monitor_sites():

    app_log.info(u"Monitoring sites for application version changes...")

    # read sites list from config
    config_site_list = config_obj["monitor_sites"]["site_list"]

    # loop over each site and check previous and current result
    for site_item in config_site_list:

        scm_name = site_item["scm_name"]
        app_name = site_item["app_name"]
        repo_name = site_item["repo_name"]

        app_log.info(u"Processing started for application %s..." % app_name)

        if scm_name == "github":

            # use github rest api to get app release info
            url = "https://api.github.com/repos/%s/%s/tags" % (app_name, repo_name)
            request_type = "get"

            # download webpage content
            status_code, content = http_client(url=url, user_agent=user_agent_chrome, request_type=request_type)

            if status_code == 0:

                content = json.loads(content)

            else:

                app_log.info(u"Problem downloading app info, skipping to new release...")
                continue

            # get tag name from json
            current_version = content[0]['name']

        elif scm_name == "aor":

            # use aor unofficial api to get app release info
            url = "https://www.archlinux.org/packages/search/json/?q=%s&arch=any&arch=x86_64" % app_name
            request_type = "get"

            # download webpage content
            status_code, content = http_client(url=url, user_agent=user_agent_chrome, request_type=request_type)

            if status_code == 0:

                content = json.loads(content)

            else:

                app_log.info(u"Problem downloading app info, skipping to new release...")
                continue

            # get package version and release number from json
            pkgver = content['results'][0]['pkgver']
            pkgrel = content['results'][0]['pkgrel']

            # construct app version
            current_version = "%s-%s" % (pkgver, pkgrel)

            last_update = content['results'][0]['last_update']

        elif scm_name == "aur":

            # use aur api to get app release info
            url = "https://aur.archlinux.org/rpc/?v=5&type=search&by=name&arg=%s" % app_name
            request_type = "get"

            # download webpage content
            status_code, content = http_client(url=url, user_agent=user_agent_chrome, request_type=request_type)

            if status_code == 0:

                content = json.loads(content)

            else:

                app_log.info(u"Problem downloading app info, skipping to new release...")
                continue

            # get app version from json
            current_version = content["results"][0]["Version"]

        # write value for current match to config
        config_obj["monitor_sites"]["%s_%s_current_version" % (scm_name, app_name)] = current_version
        config_obj.write()

        try:

            # read value from previous match from config
            previous_version = config_obj["monitor_sites"]["%s_%s_previous_version" % (scm_name, app_name)]

        except KeyError:

            app_log.info(u"No known previous version for app %s, assuming first run" % app_name)
            app_log.info(u"Setting previous version to current version %s and going to next iteration" % current_version)
            config_obj["monitor_sites"]["%s_%s_previous_version" % (scm_name, app_name)] = current_version
            config_obj.write()
            continue

        if previous_version != current_version:

            app_log.info(u"Current version %s and previous version %s are different, triggering a docker hub build (via github tag)..." % (current_version, previous_version))
            status_code, content = github_create_release(current_version, app_name)

            if status_code == 0:

                app_log.info(u"Setting previous version %s to the same as current version %s after successful build" % (previous_version, current_version))
                config_obj["monitor_sites"]["%s_%s_previous_version" % (scm_name, app_name)] = current_version
                config_obj.write()

                # send email notification
                notification_email(app_name, repo_name)

            else:

                app_log.info(u"Problem creating github release and tag, skipping to next iteration...")
                continue

        else:

            app_log.info(u"Current version %s and previous version %s match, nothing to do" % (current_version, previous_version))

        app_log.info(u"Processing finished for application %s" % app_name)

    app_log.info(u"All applications processed")

# required to prevent separate process from trying to load parent process
if __name__ == '__main__':

    app_log.info(u"Starting monitoring...")
    monitor_sites()
    app_log.info(u"Finished monitoring, exiting script...")
