import requests
import configobj
import validate
import argparse
import os
import sys
import re
import socket
import logging
import logging.handlers
import backoff
import json
import yagmail
import schedule
import time
import daemon
import urllib3
import signal
import kodijson
import datetime
from bs4 import BeautifulSoup

urllib3.disable_warnings()  # required to suppress ssl warning for urllib3 (requests uses urllib3)
signal.signal(signal.SIGINT, signal.default_int_handler)  # ensure we correctly handle all keyboard interrupts

# TODO change input to functions as dictionary
# TODO change functions to **kwargs and use .get() to get value (will be none if not fund)
# TODO change return for function to dictionary
# TODO add in option to throttle target builds for certain apps - such as jackett


def create_config():

    validator = validate.Validator()
    config_obj.validate(validator, copy=True)
    config_obj.filename = config_ini
    config_obj.write()


def time_check(current_time, grace_period_mins, source_version_change_datetime):

    # compare difference between local date/time and trigger date/time to produce timedelta
    time_delta = current_time - source_version_change_datetime
    app_logger_instance.debug(u"Time delta object is %s" % time_delta)

    # turn timedelta object into minutes
    time_delta_secs = datetime.timedelta.total_seconds(time_delta)
    time_delta_mins = int(time_delta_secs) / 60

    grace_period_mins_int = int(grace_period_mins)

    # check if time_delta is greater than or equal to grace_period_mins
    if time_delta_mins >= grace_period_mins_int:

        app_logger_instance.info(u"Time since last update (%s mins) >= to grace period (%s mins)" % (time_delta_mins, grace_period_mins))
        return True

    else:
        app_logger_instance.info(u"Time since last update (%s mins) < grace period (%s mins)" % (time_delta_mins, grace_period_mins))
        return False


def app_logging():

    # read log levels
    log_level = config_obj["general"]["log_level"]

    # setup formatting for log messages
    app_formatter = logging.Formatter("%(asctime)s %(threadName)s %(module)s %(funcName)s :: [%(levelname)s] %(message)s")

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

    elif log_level == "debug":

        app_logger.setLevel(logging.DEBUG)

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

    elif log_level == "debug":

        console_streamhandler.setLevel(logging.DEBUG)

    return {'logger': app_logger, 'handler': app_rotatingfilehandler}


def notification_email(action, source_app_name, source_repo_name, source_site_name, source_site_url, target_repo_name, previous_version, current_version):

    # read email config
    config_email_username = config_obj["notification"]["email_username"]

    if args["email_password"]:

        config_email_password = args["email_password"]

    elif config_obj["notification"]["email_password"]:

        config_email_password = config_obj["notification"]["email_password"]

    else:

        app_logger_instance.warn(u"Email notification not enabled due to missing password")
        return 1

    config_email_to = config_obj["notification"]["email_to"]
    target_repo_owner = config_obj["general"]["target_repo_owner"]

    # construct url to docker hub build details
    dockerhub_build_details = "https://hub.docker.com/r/%s/%s/tags?page=1&ordering=last_updated&name=latest" % (target_repo_owner, target_repo_name)

    # construct url to github workflow details
    github_action_details = "https://github.com/%s/%s/actions" % (target_repo_owner, target_repo_name)

    # construct url to github container registry details
    github_ghcr_details = "https://github.com/users/%s/packages/container/package/%s" % (target_repo_owner, target_repo_name)

    app_logger_instance.info(u'Sending email notification...')

    yag = yagmail.SMTP(config_email_username, config_email_password)
    subject = '%s [%s] - updated to %s' % (source_app_name, action, current_version)
    html = '''
    <b>Action:</b> %s<br>
    <b>Previous Version:</b> %s<br>
    <b>Current Version:</b> %s<br>
    <b>Source Site Name:</b> %s<br>
    <b>Source Repository:</b> %s<br>
    <b>Source App Name:</b> %s<br>
    <b>Source Site URL:</b>  <a href="%s">%s</a>
    ''' % (action, previous_version, current_version, source_site_name, source_repo_name, source_app_name, source_site_url, source_site_name)

    if action == "trigger":

        html += '''
        <b>Target Repository:</b> %s<br>
        <b>Target Github Action URL:</b> <a href="%s">github-workflow</a><br>
        <b>Target Github Container Registry URL:</b> <a href="%s">github-registry</a><br>
        <b>Target Docker Hub URL:</b> <a href="%s">dockerhub</a>
        ''' % (target_repo_name, github_action_details, github_ghcr_details, dockerhub_build_details)

    try:

        yag.send(to=config_email_to, subject=subject, contents=[html])

    except Exception:

        app_logger_instance.warning(u"Failed to send E-Mail notification to %s" % config_email_to)
        return 1


# noinspection PyUnresolvedReferences
def notification_kodi(action, source_app_name, current_version):

    # read kodi config
    kodi_username = config_obj["notification"]["kodi_username"]

    if args["kodi_password"]:

        kodi_password = args["kodi_password"]

    elif config_obj["notification"]["kodi_password"]:

        kodi_password = config_obj["notification"]["kodi_password"]

    else:

        app_logger_instance.warn(u"Kodi notification not enabled due to missing password")
        return 1

    kodi_hostname = config_obj["notification"]["kodi_hostname"]
    kodi_port = config_obj["notification"]["kodi_port"]

    # construct login with custom credentials for rpc call
    kodi = kodijson.Kodi("http://%s:%s/jsonrpc" % (kodi_hostname, kodi_port), kodi_username, kodi_password)

    # send gui notification
    try:

        kodi.GUI.ShowNotification({"title": "TriggerDockerBuild", "message": "%s [%s] - updated to %s" % (source_app_name, action, current_version)})

    except Exception:

        app_logger_instance.warning(u"Failed to send notification to Kodi instance at http://%s:%s/jsonrpc" % (kodi_hostname, kodi_port))
        return 1


@backoff.on_exception(backoff.expo, (socket.timeout, requests.exceptions.Timeout, requests.exceptions.HTTPError), max_tries=10)
def http_client(**kwargs):

    if kwargs is not None:

        if "url" in kwargs:

            url = kwargs['url']

        else:

            app_logger_instance.warning(u'No URL sent to function, exiting function...')
            return 1, None, None

        if "user_agent" in kwargs:

            user_agent = kwargs['user_agent']

        else:

            app_logger_instance.warning(u'No User Agent sent to function, exiting function...')
            return 1, None, None

        if "request_type" in kwargs:

            request_type = kwargs['request_type']

        else:

            app_logger_instance.warning(u'No request type (get/put/post) sent to function, exiting function...')
            return 1, None, None

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
        return 1, None, None

    # set connection timeout value (max time to wait for connection)
    connect_timeout = 60.0

    # set read timeout value (max time to wait between each byte)
    read_timeout = 60.0

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
            'User-Agent': user_agent,
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
        app_logger_instance.warning(u"Connection timeout for URL %s with error %s" % (url, content))
        return 1, status_code, content

    except requests.exceptions.ConnectionError as content:

        # connection error occurred
        app_logger_instance.warning(u"Connection error for URL %s with error %s" % (url, content))
        return 1, status_code, content

    except requests.exceptions.TooManyRedirects as content:

        # too many redirects, bad site or circular redirect
        app_logger_instance.warning(u"Too many retries for URL %s with error %s" % (url, content))
        return 1, status_code, content

    except requests.exceptions.HTTPError as content:

        # catch http exceptions thrown by requests
        return 1, status_code, content

    except requests.exceptions.ReadTimeout as content:
        # too many redirects, bad site or circular redirect
        app_logger_instance.warning(u"Read timeout for URL %s with error %s" % (url, content))
        return 1, status_code, content

    except requests.exceptions.RequestException as content:

        # catch any other exceptions thrown by requests
        app_logger_instance.warning(u"Caught other exceptions for URL %s with error %s" % (url, content))
        return 1, status_code, content

    else:

        if 200 <= status_code <= 299:

            app_logger_instance.info(u"The status code %s indicates a successful request for %s" % (status_code, url))
            return 0, status_code, content


def github_create_release(current_version, target_repo_owner, target_repo_name, target_access_token, user_agent_chrome):

    # remove illegal characters from version (github does not allow certain chars for release name)
    current_version = re.sub(ur":", ur".", current_version, flags=re.IGNORECASE)

    app_logger_instance.info(u"Creating Release on GitHub for version %s..." % current_version)

    github_tag_name = "%s-01" % current_version
    github_release_name = "API/URL triggered release"
    github_release_body = github_tag_name
    request_type = "post"
    http_url = 'https://api.github.com/repos/%s/%s/releases' % (target_repo_owner, target_repo_name)
    data_payload = '{"tag_name": "%s", "target_commitish": "master", "name": "%s", "body": "%s", "draft": false, "prerelease": false}' % (github_tag_name, github_release_name, github_release_body)

    # process post request
    return_code, status_code, content = http_client(url=http_url, user_agent=user_agent_chrome, additional_header={'Authorization': 'token %s' % target_access_token}, request_type=request_type, data_payload=data_payload)
    return return_code, status_code, content


def github_target_last_release_date(target_repo_owner, target_repo_name, target_access_token, user_agent_chrome):

    github_query_type = "releases/latest"
    json_query = "published_at"

    # construct url to github rest api
    url = "https://api.github.com/repos/%s/%s/%s" % (target_repo_owner, target_repo_name, github_query_type)
    request_type = "get"

    # download json content
    return_code, status_code, content = http_client(url=url, user_agent=user_agent_chrome, additional_header={'Authorization': 'token %s' % target_access_token}, request_type=request_type)

    if return_code == 0:

        try:

            content = json.loads(content)

        except (ValueError, TypeError, KeyError):

            app_logger_instance.info(u"Problem loading json from %s, skipping to next iteration..." % url)
            return 1, None

    else:

        app_logger_instance.info(u"Problem downloading json content from %s, skipping to new release..." % url)
        return 1, None

    try:

        # get release date from json
        target_last_release_date = content['%s' % json_query]

    except IndexError:

        app_logger_instance.info(u"Problem parsing json from %s, skipping to next iteration..." % url)
        return 1, None

    # convert the following then compare against throttle days value "2020-04-15T21:53:20Z"
    return 0, target_last_release_date


def github_apps(source_app_name, source_query_type, source_repo_name, target_access_token, user_agent_chrome, source_branch_name):

    # certain github repos do not have releases, only tags, thus we need to account for these differently
    if source_query_type.lower() == "tag":

        github_query_type = "tags"
        json_query = "name"

    elif source_query_type.lower() == "pre-release":

        github_query_type = "releases"
        json_query = "tag_name"

    elif source_query_type.lower() == "release":

        github_query_type = "releases/latest"
        json_query = "tag_name"

    elif source_query_type.lower() == "branch":

        github_query_type = "commits"
        json_query = "sha"

    else:

        app_logger_instance.warning(u"source_query_type '%s' is not valid, skipping to next iteration..." % source_query_type.lower())
        return 1, None, None

    # construct url to github rest api
    url = "https://api.github.com/repos/%s/%s/%s" % (source_repo_name, source_app_name, github_query_type)

    # if github branch then we specify the branch name via 'sha' parameter
    if source_query_type.lower() == "branch":

        url = "%s?sha=%s" % (url, source_branch_name)

    request_type = "get"

    # download json content
    return_code, status_code, content = http_client(url=url, user_agent=user_agent_chrome, additional_header={'Authorization': 'token %s' % target_access_token}, request_type=request_type)

    if return_code == 0:

        try:

            content = json.loads(content)

        except (ValueError, TypeError, KeyError):

            app_logger_instance.info(u"Problem loading json from %s, skipping to next iteration..." % url)
            return 1, None, None

    else:

        app_logger_instance.info(u"Problem downloading json content from %s, skipping to new release..." % url)
        return 1, None, None

    try:

        if github_query_type == "tags" or github_query_type == "commits":

            # get tag/sha from json
            current_version = content[0]['%s' % json_query]

        elif github_query_type == "releases/latest":

            # get release from json
            current_version = content['%s' % json_query]

        else:

            app_logger_instance.warning(u"Unknown Github query type of '%s', skipping to next iteration..." % github_query_type)
            return 1, None, None

    except IndexError:

        app_logger_instance.warning(u"Problem parsing json from %s, skipping to next iteration..." % url)
        return 1, None, None

    source_site_url = "https://github.com/%s/%s/%s" % (source_repo_name, source_app_name, github_query_type)

    if source_query_type.lower() == "branch":

        source_site_url = "%s/%s" % (source_site_url, source_branch_name)

    return 0, current_version, source_site_url


def aor_apps(source_app_name, user_agent_chrome):

    # use aor unofficial api to get app release info
    url = "https://www.archlinux.org/packages/search/json/?q=%s&repo=Community&repo=Core&repo=Extra&repo=Multilib&arch=any&arch=x86_64" % source_app_name
    request_type = "get"

    # download webpage content
    return_code, status_code, content = http_client(url=url, user_agent=user_agent_chrome, request_type=request_type)

    if return_code == 0:

        try:

            # decode json
            content = json.loads(content)

            # filter python objects with list comprehension to prevent fuzzy mismatch
            content = [x for x in content['results'] if x['pkgname'] == source_app_name]

        except (ValueError, TypeError, KeyError, IndexError):

            app_logger_instance.info(u"Problem loading json from %s, skipping to next iteration..." % url)
            return 1, None, None

    else:

        app_logger_instance.info(u"Problem downloading json content from %s, skipping to new release..." % url)
        return 1, None, None

    try:

        # get package version and release number from json
        pkgver = content[0]['pkgver']
        pkgrel = content[0]['pkgrel']

        # construct app version
        current_version = "%s-%s" % (pkgver, pkgrel)

        # get repo name and arch type (used in url construct for email notification)
        source_repo_name = content[0]['repo']
        source_arch_name = content[0]['arch']

    except (ValueError, TypeError, KeyError, IndexError):

        app_logger_instance.info(u"Problem parsing json from %s, skipping to next iteration..." % url)
        return 1, None, None

    source_site_url = "https://www.archlinux.org/packages/%s/%s/%s/" % (source_repo_name, source_arch_name, source_app_name)

    return 0, current_version, source_site_url


def aur_apps(source_app_name, user_agent_chrome):

    # use aur api to get app release info
    url = "https://aur.archlinux.org/rpc/?v=5&type=info&arg[]=%s" % source_app_name
    request_type = "get"

    # download webpage content
    return_code, status_code, content = http_client(url=url, user_agent=user_agent_chrome, request_type=request_type)

    if return_code == 0:

        try:

            content = json.loads(content)

        except (ValueError, TypeError, KeyError):

            app_logger_instance.info(u"Problem loading json from %s, skipping to next iteration..." % url)
            return 1, None, None

    else:

        app_logger_instance.info(u"Problem downloading json content from %s, skipping to new release..." % url)
        return 1, None, None

    try:

        # get app version from json
        current_version = content["results"][0]["Version"]

    except IndexError:

        app_logger_instance.info(u"Problem parsing json from %s, skipping to next iteration..." % url)
        return 1, None, None

    source_site_url = "https://aur.archlinux.org/packages/%s/" % source_app_name

    return 0, current_version, source_site_url


def soup_regex(source_site_url, user_agent_chrome):

    # download webpage
    request_type = "get"

    # download webpage content
    return_code, status_code, content = http_client(url=source_site_url, user_agent=user_agent_chrome, request_type=request_type)

    if return_code == 0:

        try:

            soup = BeautifulSoup(content, features="html.parser")

        except (ValueError, TypeError, KeyError):

            app_logger_instance.info(u"Problem extracting url using regex from url  %s, skipping to next iteration..." % source_site_url)
            return 1, None

    else:

        app_logger_instance.info(u"Problem downloading webpage from url  %s, skipping to new release..." % source_site_url)
        return 1, None

    return 0, soup


def monitor_sites(*arguments):

    # read sites list from config
    config_site_list = config_obj["monitor_sites"]["site_list"]
    target_repo_owner = config_obj["general"]["target_repo_owner"]

    if args["target_access_token"]:

        target_access_token = args["target_access_token"]

    elif config_obj["general"]["target_access_token"]:

        target_access_token = config_obj["general"]["target_access_token"]

    else:

        app_logger_instance.warn(u"Target Access Token is not defined via '--target-access-token' or 'config.ini'.")
        return 1

    # fake being a browser
    user_agent_chrome = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36"

    # loop over each site and check previous and current result
    for site_item in config_site_list:

        source_site_name = site_item.get("source_site_name")
        source_app_name = site_item.get("source_app_name")
        source_repo_name = site_item.get("source_repo_name")
        source_branch_name = site_item.get("source_branch_name")
        target_release_days = site_item.get("target_release_days")
        target_repo_name = site_item.get("target_repo_name")
        source_query_type = site_item.get("source_query_type")
        grace_period_mins = site_item.get("grace_period_mins")
        source_version_change_datetime = site_item.get("source_version_change_datetime")
        action = site_item.get("action")

        app_logger_instance.info(u"-------------------------------------")
        app_logger_instance.info(u"Processing started for application %s..." % source_app_name)

        if source_site_name == "github":

            return_code, current_version, source_site_url = github_apps(source_app_name, source_query_type, source_repo_name, target_access_token, user_agent_chrome, source_branch_name)

            if return_code != 0:

                app_logger_instance.warning(u"Unable to identify release, tag, or commit for repo '%s', skipping to next iteration..." % source_repo_name)
                continue

        elif source_site_name == "aor":

            # if grace period not defined then set to default value (required for aor)
            if grace_period_mins is None:

                grace_period_mins = 60

            return_code, current_version, source_site_url = aor_apps(source_app_name, user_agent_chrome)

            if return_code != 0:

                app_logger_instance.warning(u"Unable to identify current version of %s repo for app '%s', skipping to next iteration..." % (source_site_name, source_app_name))
                continue

        elif source_site_name == "aur":

            return_code, current_version, source_site_url = aur_apps(source_app_name, user_agent_chrome)

            if return_code != 0:

                app_logger_instance.warning(u"Unable to identify current version of %s repo for app '%s', skipping to next iteration..." % (source_site_name, source_app_name))
                continue

        elif source_site_name == "regex":

            if source_app_name == "minecraftbedrock":

                source_site_url = "https://www.minecraft.net/en-us/download/server/bedrock"
                return_code, soup = soup_regex(source_site_url, user_agent_chrome)

                if return_code != 0:

                    app_logger_instance.info(u"Problem parsing webpage using beautiful soup for url  %s, skipping to next iteration..." % source_site_url)
                    continue

                try:

                    # get download url from soup
                    url_line = soup.select('a[data-platform="serverBedrockLinux"]')
                    download_url = url_line[0]['href']

                except IndexError:

                    app_logger_instance.warning(u"Unable to identify download url using beautiful soup for app %s, skipping to next iteration..." % source_app_name)
                    continue

                try:

                    # get app version from soup
                    current_version = re.search(r"[\d.]+(?=.zip)", download_url).group()

                except IndexError:

                    request_type = "get"
                    github_fallback_version_url = "https://raw.githubusercontent.com/ich777/docker-minecraft-bedrock/master/version"
                    return_code, status_code, content = http_client(url=github_fallback_version_url,user_agent=user_agent_chrome,request_type=request_type)

                    if return_code != 0:

                        app_logger_instance.warning(u"Unable to identify app version using beautiful soup for app %s, skipping to next iteration..." % source_app_name)
                        continue

                    else:

                        current_version = content

            elif source_app_name == "minecraftserver":

                source_site_url = "https://www.minecraft.net/en-us/download/server"
                return_code, soup = soup_regex(source_site_url, user_agent_chrome)

                if return_code != 0:

                    app_logger_instance.info(u"Problem parsing webpage using beautiful soup for url  %s, skipping to next iteration..." % source_site_url)
                    continue

                try:

                    # get download url from soup
                    url_line = soup.select('a[aria-label="mincraft version"]')[0]
                    download_url = url_line['href']

                except IndexError:

                    app_logger_instance.debug(u"Unable to identify download url using beautiful soup for app %s, ignoring..." % source_app_name)

                try:

                    # get download url from soup
                    url_line = soup.select('a[aria-label="mincraft version"]')[0]
                    url_line_string = str(url_line)

                    # get app version from soup
                    current_version = re.search(r"[\d]+[\d.]+(?=.jar)", url_line_string).group()

                except IndexError:

                    app_logger_instance.warning(u"Unable to identify version using beautiful soup for app %s, skipping to next iteration..." % source_app_name)
                    continue

            else:

                app_logger_instance.warning(u"Source site app %s unknown, skipping to next iteration..." % source_app_name)
                continue

        else:

            app_logger_instance.warning(u"Source site name %s unknown, skipping to next iteration..." % source_site_name)
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

            if action == "trigger":

                current_datetime_object = datetime.datetime.now()
                current_datetime_str = current_datetime_object.strftime('%Y-%m-%d %H:%M:%S')

                if grace_period_mins:

                    if source_version_change_datetime is None:

                        app_logger_instance.debug(u"Trigger datetime not defined in config.ini, creating from current datetime")
                        source_version_change_datetime = current_datetime_str

                        site_item["source_version_change_datetime"] = source_version_change_datetime
                        config_obj.write()
                        continue

                    # run function to check if time since last source change is greater than or equal to grace period
                    else:

                        source_version_change_datetime_object = datetime.datetime.strptime(source_version_change_datetime, '%Y-%m-%d %H:%M:%S')

                        if not time_check(current_datetime_object, grace_period_mins, source_version_change_datetime_object):

                            app_logger_instance.info(u"Source version change for app '%s' is less than '%s' mins ago, skipping to next iteration..." % (source_app_name, grace_period_mins))
                            continue

                        else:

                            app_logger_instance.info(u"Source version change for app '%s' is >= '%s' mins ago, proceeding..." % (source_app_name, grace_period_mins))

                if target_release_days:

                    return_code, last_release_date = github_target_last_release_date(target_repo_owner, target_repo_name, target_access_token, user_agent_chrome)

                    if return_code != 0:

                        app_logger_instance.warning(u"Unable to identify target release date for repo '%s', skipping to next iteration..." % target_repo_name)
                        continue

                    target_release_date_object = datetime.datetime.strptime(last_release_date, '%Y-%m-%dT%H:%M:%SZ')

                    # compare difference between local date/time and trigger date/time to produce timedelta
                    target_time_delta = current_datetime_object - target_release_date_object

                    # extract days from time delta
                    target_time_delta_days = target_time_delta.days
                    app_logger_instance.debug(u"Minimum days between target releases is '%s' days" % target_release_days)
                    app_logger_instance.debug(u"Last target release was '%s' days ago" % target_time_delta_days)

                    if int(target_time_delta_days) >= int(target_release_days):

                        app_logger_instance.info(u"Last target release date for app '%s' is >= '%s' days ago, proceeding..." % (target_repo_name, target_release_days))

                    else:

                        app_logger_instance.info(u"Last target release date for app '%s' is less than '%s' days ago, skipping to next iteration..." % (target_repo_name, target_release_days))
                        continue

                app_logger_instance.info(u"Previous version %s and current version %s are different, triggering a docker hub build (via github tag)..." % (previous_version, current_version))
                return_code, status_code, content = github_create_release(current_version, target_repo_owner, target_repo_name, target_access_token, user_agent_chrome)

                if status_code == 201:

                    app_logger_instance.info(u"Setting previous version %s to the same as current version %s after successful build" % (previous_version, current_version))

                elif status_code == 422:

                    app_logger_instance.warning(u"GitHub release already exists for %s/%s, overwriting current version and skipping to next iteration..." % (target_repo_owner, target_repo_name))
                    app_logger_instance.debug(u"Writing current version %s to config.ini" % current_version)
                    config_obj["results"]["%s_%s_%s_previous_version" % (source_site_name, source_app_name, target_repo_name)] = current_version
                    config_obj.write()
                    continue

                else:

                    app_logger_instance.warning(u"Problem creating github release and tag, skipping to next iteration...")
                    continue

                if source_version_change_datetime is not None:

                    app_logger_instance.debug(u"Deleting 'source_version_change_datetime', used next time version change occurs")
                    del site_item["source_version_change_datetime"]
                    config_obj.write()

                app_logger_instance.debug(u"Creating 'target_trigger_datetime', used to track when trigger of docker build happened")
                site_item["target_trigger_datetime"] = current_datetime_str
                config_obj.write()

            elif action == "notify":

                app_logger_instance.info(u"Previous version %s and current version %s are different" % (previous_version, current_version))

            app_logger_instance.debug(u"Writing current version %s to config.ini" % current_version)
            config_obj["results"]["%s_%s_%s_previous_version" % (source_site_name, source_app_name, target_repo_name)] = current_version
            config_obj.write()

            if args["email_notification"] is True:

                # send email notification
                app_logger_instance.info(u"Sending email notification...")
                notification_email(action, source_app_name, source_repo_name, source_site_name, source_site_url, target_repo_name, previous_version, current_version)

            if args["kodi_notification"] is True:

                # send kodi notification
                app_logger_instance.info(u"Sending kodi notification...")
                notification_kodi(action, source_app_name, current_version)

        else:

            app_logger_instance.info(u"Previous version %s and current version %s match, nothing to do" % (previous_version, current_version))

        app_logger_instance.info(u"Processing finished for application %s" % source_app_name)

    # write timestamp to config.ini
    config_obj["general"]["last_check"] = time.strftime("%c")
    config_obj.write()


def ondemand_start():

    app_logger_instance.info(u"Checking for version changes...")
    monitor_sites()


def scheduler_start():

    schedule_check_mins = config_obj["general"]["schedule_check_mins"]

    # now run monitor_sites function via scheduler
    schedule.every(schedule_check_mins).minutes.do(monitor_sites)

    while True:

        try:

            schedule.run_pending()
            app_logger_instance.info(u"All applications processed, waiting for next invocation in %s minutes..." % schedule_check_mins)
            time.sleep(1)

        except KeyboardInterrupt:

            app_logger_instance.info(u"Keyboard interrupt received, exiting script...")
            sys.exit()


# required to prevent separate process from trying to load parent process
if __name__ == '__main__':

    version = "1.1.0"

    # custom argparse to redirect user to help if unknown argument specified
    class ArgparseCustom(argparse.ArgumentParser):

        def error(self, message):
            sys.stderr.write('error: %s\n' % message)
            self.print_help()
            sys.exit(2)

    # setup argparse description and usage, also increase spacing for help to 50
    commandline_parser = ArgparseCustom(prog="TriggerDockerBuild", description="%(prog)s " + version, usage="%(prog)s [--help] [--config <path>] [--logs <path>] [--kodi-password <password>] [--email-password <password>] [--target-access-token <token>] [--pidfile <path>] [--kodi-notification] [--email-notification] [--schedule] [--daemon] [--version]", formatter_class=lambda prog: argparse.HelpFormatter(prog, max_help_position=50))

    # add argparse command line flags
    commandline_parser.add_argument(u"--config", metavar=u"<path>", help=u"specify path for config file e.g. --config /opt/triggerdockerbuild/config/")
    commandline_parser.add_argument(u"--logs", metavar=u"<path>", help=u"specify path for log files e.g. --logs /opt/triggerdockerbuild/logs/")
    commandline_parser.add_argument(u"--kodi-password", metavar=u"<password>", help=u"specify the password to access kodi e.g. --kodi-password foo")
    commandline_parser.add_argument(u"--email-password", metavar=u"<password>", help=u"specify the email account password e.g. --email-password foo")
    commandline_parser.add_argument(u"--target-access-token", metavar=u"<token>", help=u"specify the github personal access token e.g. --target-access-token 123456789")
    commandline_parser.add_argument(u"--pidfile", metavar=u"<path>", help=u"specify path to pidfile e.g. --pid /var/run/triggerdockerbuild/triggerdockerbuild.pid")
    commandline_parser.add_argument(u"--kodi-notification", action=u"store_true", help=u"enable kodi notification e.g. --kodi-notification")
    commandline_parser.add_argument(u"--email-notification", action=u"store_true", help=u"enable email notification e.g. --email-notification")
    commandline_parser.add_argument(u"--schedule", action=u"store_true", help=u"enable scheduling e.g. --schedule")
    commandline_parser.add_argument(u"--daemon", action=u"store_true", help=u"run as daemonized process e.g. --daemon")
    commandline_parser.add_argument(u"--version", action=u"version", version=version)

    # save arguments in dictionary
    args = vars(commandline_parser.parse_args())

    # set path to root folder for application
    app_root_dir = os.path.dirname(os.path.realpath(__file__)).decode("utf-8")

    # set path for configspec.ini file
    configspec_ini = os.path.join(app_root_dir, u"configs/configspec.ini")

    if not args["config"]:

        # set folder path for config files
        config_dir = os.path.join(app_root_dir, u"configs")
        config_dir = os.path.normpath(config_dir)

    else:

        config_dir = args["config"]

    # set path for config.ini file
    config_ini = os.path.join(config_dir, u"config.ini")

    # create config and logs paths if they dont exist
    if not os.path.exists(config_dir):
        os.makedirs(config_dir)

    if not args["logs"]:

        # set folder path for log files
        logs_dir = os.path.join(app_root_dir, u"logs")
        logs_dir = os.path.normpath(logs_dir)

    else:

        logs_dir = args["logs"]

    # set path for log file
    app_log_file = os.path.join(logs_dir, u"app.log")

    if not os.path.exists(logs_dir):

        os.makedirs(logs_dir)

    # create configobj instance, set config.ini file, set encoding and set configspec.ini file
    config_obj = configobj.ConfigObj(config_ini, list_values=False, write_empty_values=True, encoding='UTF-8', default_encoding='UTF-8', configspec=configspec_ini, unrepr=True)

    # create config.ini
    create_config()

    # setup logging
    app_log = app_logging()
    app_logger_instance = app_log.get('logger')
    app_handler = app_log.get('handler')

    # check os is not windows and then run main process as daemonized process
    if args["daemon"] is True and os.name != "nt":

        app_logger_instance.info(u"Running as a daemonized process...")

        # specify the logging handler as an exclusion to the daemon, to prevent its output being closed
        daemon_context = daemon.DaemonContext()
        daemon_context.files_preserve = [app_handler.stream]
        daemon_context.open()

    else:

        app_logger_instance.info(u"Running as a foreground process...")

    if args["schedule"] is True:

        app_logger_instance.info(u"Running via schedule...")
        scheduler_start()

    else:

        app_logger_instance.info(u"Running on demand...")
        ondemand_start()
