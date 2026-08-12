import argparse
import datetime
import html as _html  # aliased because notification_email uses 'html' as local var
import json
import logging
import logging.handlers
import os
import re
import signal
import sys
import time

import configobj
import daemon
import kodijson
import requests
import schedule
import urllib3
import validate
import yagmail
from bs4 import BeautifulSoup

signal.signal(signal.SIGINT, signal.default_int_handler)  # ensure we correctly handle all keyboard interrupts

# TODO change input to functions as dictionary
# TODO change functions to **kwargs and use .get() to get value (will be none if not fund)
# TODO change return for function to dictionary

# Persistent notification state — survives between scheduler invocations while the process is running.
# Keyed by site_name (str). Each entry tracks whether a site was last known down and when the most
# recent notification was sent, so we only alert on state transitions and suppress repeat emails.
_site_down_state: dict = {}

# Per-site app-failure counters — also module-level so they accumulate across scheduler runs.
# Keyed by site_name (str); reset to 0 when any app for that site succeeds.
_app_down_counters: dict = {}

# Whether to verify TLS certificates on HTTPS requests. Defaults to True (secure).
# Set to False in config.ini only when behind an SSL-inspection proxy that uses
# self-signed certificates (e.g. corporate MITM).
verify_ssl = True


def _silence_tls_warnings(verify_ssl_enabled):
    """Suppress urllib3's InsecureRequestWarning when TLS verification is off.

    When the user explicitly sets verify_ssl = False (e.g. behind an
    SSL-inspection proxy), the resulting InsecureRequestWarning for every HTTPS
    request is expected noise. Keep warnings visible whenever verification is
    enabled (the secure default) so TLS problems are never hidden.
    """
    if not verify_ssl_enabled:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def create_config():

    validator = validate.Validator()
    config_obj.validate(validator, copy=True)
    config_obj.filename = config_ini
    config_obj.write()


def time_check(current_time, grace_period_mins, source_version_change_datetime):

    # compare difference between local date/time and trigger date/time to produce timedelta
    time_delta = current_time - source_version_change_datetime
    app_logger_instance.debug("Time delta object is %s" % time_delta)

    # turn timedelta object into minutes
    time_delta_secs = datetime.timedelta.total_seconds(time_delta)
    time_delta_mins = int(time_delta_secs) / 60

    grace_period_mins_int = int(grace_period_mins)

    # check if time_delta is greater than or equal to grace_period_mins
    if time_delta_mins >= grace_period_mins_int:
        app_logger_instance.info(
            "Time since last update (%s mins) >= to grace period (%s mins)" % (time_delta_mins, grace_period_mins)
        )
        return True

    else:
        app_logger_instance.info(
            "Time since last update (%s mins) < grace period (%s mins)" % (time_delta_mins, grace_period_mins)
        )
        return False


def app_logging():

    # read log levels
    log_level = config_obj["general"]["log_level"]

    # setup formatting for log messages
    app_formatter = logging.Formatter(
        "%(asctime)s %(threadName)s %(module)s %(funcName)s :: [%(levelname)s] %(message)s"
    )

    # setup logger for app
    app_logger = logging.getLogger("app")

    # add rotating log handler
    app_rotatingfilehandler = logging.handlers.RotatingFileHandler(
        app_log_file, "a", maxBytes=10485760, backupCount=3, encoding="utf-8"
    )

    # set formatter for app
    app_rotatingfilehandler.setFormatter(app_formatter)

    # add the log message handler to the logger
    app_logger.addHandler(app_rotatingfilehandler)

    # set level of logging from config (case-insensitive)
    log_level_upper = log_level.upper()

    if log_level_upper == "INFO":
        app_logger.setLevel(logging.INFO)

    elif log_level_upper == "WARNING":
        app_logger.setLevel(logging.WARNING)

    elif log_level_upper == "ERROR":
        app_logger.setLevel(logging.ERROR)

    elif log_level_upper == "DEBUG":
        app_logger.setLevel(logging.DEBUG)

    else:
        # unrecognised level — default to WARNING
        app_logger.setLevel(logging.WARNING)
        app_logger.warning("Unrecognised log level '%s', defaulting to WARNING" % log_level)

    # setup logging to console
    console_streamhandler = logging.StreamHandler()

    # set formatter for console
    console_streamhandler.setFormatter(app_formatter)

    # add handler for formatter to the console
    app_logger.addHandler(console_streamhandler)

    # set level of logging from config for console (case-insensitive)
    if log_level_upper == "INFO":
        console_streamhandler.setLevel(logging.INFO)

    elif log_level_upper == "WARNING":
        console_streamhandler.setLevel(logging.WARNING)

    elif log_level_upper == "ERROR":
        console_streamhandler.setLevel(logging.ERROR)

    elif log_level_upper == "DEBUG":
        console_streamhandler.setLevel(logging.DEBUG)

    return {"logger": app_logger, "handler": app_rotatingfilehandler}


def notification_email(**kwargs):

    if not email_notification:
        app_logger_instance.info("Email notification not enabled")
        return 1

    # unpack arguments from dictionary and HTML-escape for safe email rendering
    _e = _html.escape
    action = kwargs.get("action")
    msg_type = kwargs.get("msg_type")
    error_msg = _e(kwargs.get("error_msg") or "")
    source_app_name = _e(kwargs.get("source_app_name") or "")
    source_repo_name = _e(kwargs.get("source_repo_name") or "")
    source_site_name = _e(kwargs.get("source_site_name") or "")
    # Fall back to placeholder when source_site_url is None so emails don't render 'None'.
    # Note: source_site_url is used in href attributes — do NOT escape it.
    source_site_url = kwargs.get("source_site_url") or "(unknown)"
    target_repo_name = _e(kwargs.get("target_repo_name") or "")
    previous_version = _e(kwargs.get("previous_version") or "")
    current_version = _e(kwargs.get("current_version") or "")

    if msg_type == "site_error":
        yag = yagmail.SMTP(email_username, email_password)
        subject = "%s - %s" % (source_site_name, msg_type)
        html = """
        <b>Source Site Name:</b> %s<br>
        <b>Source Site URL:</b>  <a href="%s">%s</a><br>
        <b>Error Message:</b> %s
        """ % (source_site_name, source_site_url, source_site_name, error_msg)

    elif msg_type == "site_recovered":
        yag = yagmail.SMTP(email_username, email_password)
        subject = "%s - site recovered" % source_site_name
        html = """
        <b>Source Site Name:</b> %s<br>
        <b>Source Site URL:</b>  <a href="%s">%s</a><br>
        <b>Message:</b> %s
        """ % (source_site_name, source_site_url, source_site_name, error_msg)

    elif msg_type == "config_error" or msg_type == "app_error":
        yag = yagmail.SMTP(email_username, email_password)
        subject = "%s - %s" % (source_app_name, msg_type)
        html = """
        <b>Source Site Name:</b> %s<br>
        <b>Source Repository:</b> %s<br>
        <b>Source Site URL:</b>  <a href="%s">%s</a><br>
        <b>Error Message:</b> %s
        """ % (source_site_name, source_repo_name, source_site_url, source_app_name, error_msg)

    else:
        target_repo_owner = config_obj["general"]["target_repo_owner"]

        # construct url to docker hub build details
        dockerhub_build_details = "https://hub.docker.com/r/%s/%s/tags?page=1&ordering=last_updated&name=latest" % (
            target_repo_owner,
            target_repo_name,
        )

        # construct url to github workflow details
        github_action_details = "https://github.com/%s/%s/actions" % (target_repo_owner, target_repo_name)

        # construct url to github container registry details
        github_ghcr_details = "https://github.com/users/%s/packages/container/package/%s" % (
            target_repo_owner,
            target_repo_name,
        )

        yag = yagmail.SMTP(email_username, email_password)
        subject = "%s [%s] - updated to %s" % (source_app_name, action, current_version)
        html = """
        <b>Action:</b> %s<br>
        <b>Previous Version:</b> %s<br>
        <b>Current Version:</b> %s<br>
        <b>Source Site Name:</b> %s<br>
        <b>Source Repository:</b> %s<br>
        <b>Source Site URL:</b>  <a href="%s">%s</a>
        """ % (
            action,
            previous_version,
            current_version,
            source_site_name,
            source_repo_name,
            source_site_url,
            source_app_name,
        )

        if action == "trigger":
            html += """
            <b>Target Repository URL:</b> <a href="https://github.com/%s/%s">github repo</a><br>
            <b>Target Github Action URL:</b> <a href="%s">github workflow</a><br>
            <b>Target Github Container Registry URL:</b> <a href="%s">github registry</a><br>
            <b>Target Docker Hub Registry URL:</b> <a href="%s">dockerhub registry</a>
            """ % (
                target_repo_owner,
                target_repo_name,
                github_action_details,
                github_ghcr_details,
                dockerhub_build_details,
            )

    try:
        app_logger_instance.info("Sending email notification...")
        yag.send(to=email_to, subject=subject, contents=[html])
        return 0

    except Exception:
        app_logger_instance.warning("Failed to send E-Mail notification to %s" % email_to)
        return 1

    return 1


# noinspection PyUnresolvedReferences
def notification_kodi(action, source_app_name, current_version):

    if not kodi_notification:
        app_logger_instance.info("Kodi notification not enabled")
        return 1

    # read kodi config
    kodi_username = config_obj["notification"]["kodi_username"]
    kodi_hostname = config_obj["notification"]["kodi_hostname"]
    kodi_port = config_obj["notification"]["kodi_port"]

    # construct login with custom credentials for rpc call
    kodi = kodijson.Kodi("http://%s:%s/jsonrpc" % (kodi_hostname, kodi_port), kodi_username, kodi_password)

    # send gui notification
    try:
        app_logger_instance.info("Sending kodi notification...")
        kodi.GUI.ShowNotification(
            {
                "title": "TriggerDockerBuild",
                "message": "%s [%s] - updated to %s" % (source_app_name, action, current_version),
            }
        )

    except Exception:
        app_logger_instance.warning(
            "Failed to send notification to Kodi instance at http://%s:%s/jsonrpc" % (kodi_hostname, kodi_port)
        )
        return 1


def http_client(**kwargs):

    if kwargs is not None:
        if "url" in kwargs:
            url = kwargs["url"]

        else:
            app_logger_instance.warning("No URL sent to function, exiting function...")
            return 1, None, None

        if "user_agent" in kwargs:
            user_agent = kwargs["user_agent"]

        else:
            app_logger_instance.warning("No User Agent sent to function, exiting function...")
            return 1, None, None

        if "request_type" in kwargs:
            request_type = kwargs["request_type"]

        else:
            app_logger_instance.warning("No request type (get/put/post) sent to function, exiting function...")
            return 1, None, None

        # optional stuff to include
        if "auth" in kwargs:
            auth = kwargs["auth"]

        else:
            auth = None

        if "additional_header" in kwargs:
            additional_header = kwargs["additional_header"]

        else:
            additional_header = None

        if "data_payload" in kwargs:
            data_payload = kwargs["data_payload"]

        else:
            data_payload = None

        # JSON payload dict — sent via requests json= kwarg which automatically
        # sets Content-Type: application/json (required by the GitHub API).
        if "json_payload" in kwargs:
            json_payload = kwargs["json_payload"]

        else:
            json_payload = None

    else:
        app_logger_instance.warning("No keyword args sent to function, exiting function...")
        return 1, None, None

    # set connection timeout value (max time to wait for connection)
    connect_timeout = 60.0

    # set read timeout value (max time to wait between each byte)
    read_timeout = 60.0

    # use a session instance to customize how "requests" handles making http requests
    session = requests.Session()

    # Default to verifying SSL certificates. Users behind proxies with custom CA bundles
    # can set REQUESTS_CA_BUNDLE or SSL_CERT_FILE environment variables instead of disabling.
    # Set verify_ssl = False in config.ini to disable verification entirely
    # (only for environments with self-signed certs from SSL-inspection proxies).
    effective_verify_ssl = kwargs.get("verify_ssl", globals().get("verify_ssl", True))

    # set status_code and content to None in case nothing returned
    status_code = None

    try:
        # define dict of common arguments for requests
        requests_data_dict = {
            "url": url,
            "timeout": (connect_timeout, read_timeout),
            "allow_redirects": True,
            "verify": effective_verify_ssl,
        }

        # define default headers to compress and fake user agent
        session.headers.update({"Accept-encoding": "gzip", "User-Agent": user_agent})

        if "additional_header" in kwargs:
            additional_header = kwargs["additional_header"]

            # Skip update when header is None/empty (e.g. non-GitHub site checks)
            if additional_header:
                session.headers.update(additional_header)

        if "auth" in kwargs:
            session.auth = auth

        if request_type in ("put", "post"):
            # add additional keyword arguments
            if json_payload is not None:
                # requests json= kwarg sets Content-Type: application/json
                requests_data_dict.update({"json": json_payload})
            else:
                requests_data_dict.update({"data": data_payload})

        # construct class.method from request_type
        request_method = getattr(session, request_type)

        # Transient server errors (5xx) may succeed on retry — e.g. 502/503/504
        # when a source site is under load (DDoS). Retry a bounded number of
        # times with a short delay. Client errors (4xx) are never retried and
        # fall through to the status-code handling below.
        transient_statuses = (502, 503, 504)
        max_attempts = 3
        retry_delay_secs = 5

        for attempt in range(max_attempts):
            # use keyword argument unpack to convert dict to keyword args
            response = request_method(**requests_data_dict)

            # get status code and content returned
            status_code = response.status_code
            content = response.content

            if status_code in transient_statuses and attempt < max_attempts - 1:
                app_logger_instance.warning(
                    "Transient HTTP status %s from %s, retrying (%d/%d)..."
                    % (status_code, url, attempt + 1, max_attempts)
                )
                time.sleep(retry_delay_secs)
            else:
                break

        if status_code == 401:
            app_logger_instance.warning(
                "The status code %s indicates unauthorised access for %s, error is %s" % (status_code, url, content)
            )
            raise requests.exceptions.HTTPError(status_code, url, content)

        elif status_code == 404:
            app_logger_instance.warning(
                "The status code %s indicates the requested resource could not be found  for %s, error is %s"
                % (status_code, url, content)
            )
            raise requests.exceptions.HTTPError(status_code, url, content)

        elif status_code == 422:
            app_logger_instance.warning(
                "The status code %s indicates a request was well-formed but was unable "
                "to be followed due to semantic errors for %s, error is %s" % (status_code, url, content)
            )
            raise requests.exceptions.HTTPError(status_code, url, content)

        elif not 200 <= status_code <= 299:
            app_logger_instance.warning(
                "The status code %s indicates an unexpected error for %s, error is %s" % (status_code, url, content)
            )
            raise requests.exceptions.HTTPError(status_code, url, content)

    except requests.exceptions.ConnectTimeout as content:
        # connect timeout occurred
        app_logger_instance.warning("Connection timeout for URL %s with error %s" % (url, content))
        return 1, status_code, content

    except requests.exceptions.ConnectionError as content:
        # connection error occurred
        app_logger_instance.warning("Connection error for URL %s with error %s" % (url, content))
        return 1, status_code, content

    except requests.exceptions.TooManyRedirects as content:
        # too many redirects, bad site or circular redirect
        app_logger_instance.warning("Too many retries for URL %s with error %s" % (url, content))
        return 1, status_code, content

    except requests.exceptions.HTTPError as content:
        # catch http exceptions thrown by requests
        return 1, status_code, content

    except requests.exceptions.ReadTimeout as content:
        # read timeout occurred
        app_logger_instance.warning("Read timeout for URL %s with error %s" % (url, content))
        return 1, status_code, content

    except requests.exceptions.RequestException as content:
        # catch any other exceptions thrown by requests
        app_logger_instance.warning("Caught other exceptions for URL %s with error %s" % (url, content))
        return 1, status_code, content

    else:
        if 200 <= status_code <= 299:
            app_logger_instance.info("The status code %s indicates a successful request for %s" % (status_code, url))
            return 0, status_code, content


def github_create_release(current_version, target_repo_branch, target_repo_owner, target_repo_name, user_agent):
    """Create a GitHub release via the REST API.

    Sends JSON via requests json= kwarg which automatically sets
    Content-Type: application/json (required by the GitHub API).

    Returns (return_code, status_code, content).
    """

    # remove illegal characters from version (github does not allow certain chars for release name)
    current_version = re.sub(r":", r".", current_version)

    app_logger_instance.info("Creating Release on GitHub for version %s..." % current_version)

    github_tag_name = "%s-01" % current_version
    github_release_name = "API/URL triggered release"
    github_release_body = github_tag_name
    request_type = "post"
    http_url = "https://api.github.com/repos/%s/%s/releases" % (target_repo_owner, target_repo_name)
    # JSON dict sent via requests json= kwarg → Content-Type: application/json
    # (required by the GitHub API; data= without the header causes 422 errors)
    json_payload = {
        "tag_name": github_tag_name,
        "target_commitish": target_repo_branch,
        "name": github_release_name,
        "body": github_release_body,
        "draft": False,
        "prerelease": False,
    }

    # process post request
    return_code, status_code, content = http_client(
        url=http_url,
        user_agent=user_agent,
        additional_header={"Authorization": "token %s" % target_access_token},
        request_type=request_type,
        json_payload=json_payload,
    )
    return return_code, status_code, content


def check_site(**kwargs):

    # unpack arguments from dictionary
    url = kwargs.get("url")
    user_agent = kwargs.get("user_agent")
    site_name = kwargs.get("site_name")
    # Hours to wait before sending a "still down" reminder while a site remains degraded.
    # Prevents a flood of repeat emails across scheduler runs during a prolonged outage.
    notification_cooldown_hours = kwargs.get("notification_cooldown_hours", 4)

    request_type = "get"

    # set number of retries and set default site_down boolean
    retries = 10
    sleep_secs = 60
    site_down = True

    while True:
        # Only send GitHub PAT to GitHub — not to third-party sites like GitLab, PyPI, AOR, or AUR.
        if site_name.lower() == "github":
            auth_header = {"Authorization": "token %s" % target_access_token}
        else:
            auth_header = None

        # download json content
        return_code, status_code, content = http_client(
            url=url, user_agent=user_agent, additional_header=auth_header, request_type=request_type
        )

        if return_code == 0:
            site_down = False
            app_logger_instance.debug(f"'{site_name}' site operational for '{url}'")
            break
        else:
            app_logger_instance.info(
                f"Having issues connecting to '{site_name}' for '{url}', retrying in '{sleep_secs}' seconds..."
            )
            time.sleep(sleep_secs)
            retries = retries - 1

        if retries <= 0:
            app_logger_instance.warning(f"'{site_name}' site down for '{url}'")
            break

    # Retrieve previous notification state for this site (defaults to "up, never notified")
    previous_state = _site_down_state.get(site_name, {"is_down": False, "notified_at": None})
    was_down = previous_state["is_down"]
    last_notified = previous_state.get("notified_at")

    if site_down:
        if not was_down:
            # Transition: UP → DOWN — send first-time alert and record state
            msg_type = "site_error"
            error_msg = f"{site_name} site down - '{url}'"
            notification_email(msg_type=msg_type, error_msg=error_msg, source_site_name=site_name, source_site_url=url)
            app_logger_instance.warning(error_msg)
            _site_down_state[site_name] = {"is_down": True, "notified_at": datetime.datetime.now(datetime.UTC)}

        else:
            # Site was already known down — only re-notify after the cooldown period elapses
            now = datetime.datetime.now(datetime.UTC)
            hours_since_notif = (
                ((now - last_notified).total_seconds() / 3600) if last_notified else notification_cooldown_hours
            )

            if hours_since_notif >= notification_cooldown_hours:
                msg_type = "site_error"
                error_msg = (
                    f"{site_name} site still down - '{url}' (ongoing issue, last notified {hours_since_notif:.1f}h ago)"
                )
                notification_email(
                    msg_type=msg_type, error_msg=error_msg, source_site_name=site_name, source_site_url=url
                )
                app_logger_instance.warning(error_msg)
                _site_down_state[site_name] = {"is_down": True, "notified_at": now}

            else:
                app_logger_instance.info(
                    f"'{site_name}' already known down, suppressing repeat notification "
                    f"(last notified {hours_since_notif:.1f}h ago, cooldown {notification_cooldown_hours}h)"
                )

    else:
        if was_down:
            # Transition: DOWN → UP — send a recovery notification and clear state
            msg_type = "site_recovered"
            recovery_msg = f"{site_name} site has recovered - '{url}'"
            notification_email(
                msg_type=msg_type, error_msg=recovery_msg, source_site_name=site_name, source_site_url=url
            )
            app_logger_instance.info(recovery_msg)

        _site_down_state[site_name] = {"is_down": False, "notified_at": None}

    return site_down


def github_target_last_release_date(target_repo_owner, target_repo_name, user_agent):

    github_query_type = "releases/latest"
    json_query = "published_at"

    # construct url to github rest api
    url = "https://api.github.com/repos/%s/%s/%s" % (target_repo_owner, target_repo_name, github_query_type)
    request_type = "get"

    # download json content
    return_code, status_code, content = http_client(
        url=url,
        user_agent=user_agent,
        additional_header={"Authorization": "token %s" % target_access_token},
        request_type=request_type,
    )

    if return_code == 0:
        try:
            content = json.loads(content)

        except (ValueError, TypeError, KeyError):
            app_logger_instance.info("Problem loading json from %s" % url)
            return 1, None

    else:
        app_logger_instance.info("Problem downloading json content from %s" % url)
        return 1, None

    try:
        # get release date from json
        target_last_release_date = content["%s" % json_query]

    except (IndexError, KeyError):
        app_logger_instance.info("Problem parsing json from %s, skipping to next iteration..." % url)
        return 1, None

    # convert the following then compare against throttle days value "2020-04-15T21:53:20Z"
    return 0, target_last_release_date


def github_apps(source_app_name, source_query_type, source_repo_name, user_agent, source_branch_name):

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
        app_logger_instance.warning(
            "source_query_type '%s' is not valid, skipping to next iteration..." % source_query_type.lower()
        )
        return None, None

    # construct url for package details
    source_site_url = "https://github.com/%s/%s/%s" % (source_repo_name, source_app_name, github_query_type)

    # construct url to github rest api
    url = "https://api.github.com/repos/%s/%s/%s" % (source_repo_name, source_app_name, github_query_type)

    # if github branch then we specify the branch name via 'sha' parameter
    if source_query_type.lower() == "branch":
        url = "%s?sha=%s" % (url, source_branch_name)

    request_type = "get"

    # download json content
    return_code, status_code, content = http_client(
        url=url,
        user_agent=user_agent,
        additional_header={"Authorization": "token %s" % target_access_token},
        request_type=request_type,
    )

    if return_code == 0:
        try:
            content = json.loads(content)

        except (ValueError, TypeError, KeyError):
            app_logger_instance.info("Problem loading json from %s" % url)
            return None, source_site_url

    else:
        app_logger_instance.info("Problem downloading json content from %s" % url)
        return None, source_site_url

    try:
        if github_query_type in ("tags", "commits", "releases"):
            # get tag/sha from json
            current_version = content[0]["%s" % json_query]

        elif github_query_type == "releases/latest":
            # get release from json
            current_version = content["%s" % json_query]

        else:
            app_logger_instance.warning(
                "Unknown Github query type of '%s', skipping to next iteration..." % github_query_type
            )
            return None, source_site_url

    except (IndexError, KeyError):
        app_logger_instance.warning("Problem parsing json from %s, skipping to next iteration..." % url)
        return None, source_site_url

    if source_query_type.lower() == "branch":
        source_site_url = "%s/%s" % (source_site_url, source_branch_name)

    return current_version, source_site_url


def gitlab_apps(
    source_app_name, source_repo_name, source_project_id, source_branch_name, source_query_type, user_agent
):

    # use gitlab rest api
    url = "https://gitlab.com/api/v4/projects/%s/repository/commits/%s" % (source_project_id, source_branch_name)

    # construct url for package details
    source_site_url = "https://gitlab.com/%s/%s" % (source_repo_name, source_app_name)

    request_type = "get"

    if source_query_type.lower() == "branch":
        json_query = "id"

    else:
        app_logger_instance.warning(
            "source_query_type '%s' is not valid, skipping to next iteration..." % source_query_type.lower()
        )
        return None, source_site_url

    # download webpage content
    return_code, status_code, content = http_client(url=url, user_agent=user_agent, request_type=request_type)

    if return_code == 0:
        try:
            # decode json
            content = json.loads(content)

        except (ValueError, TypeError, KeyError, IndexError):
            app_logger_instance.info("Problem loading json from %s" % url)
            return None, source_site_url

    else:
        app_logger_instance.info("Problem downloading json content from %s" % url)
        return None, source_site_url

    try:
        # construct app version
        current_version = content["%s" % json_query]

    except (ValueError, TypeError, KeyError, IndexError):
        app_logger_instance.info("Problem parsing json from %s, skipping to next iteration..." % url)
        return None, source_site_url

    return current_version, source_site_url


def pypi_apps(source_app_name, user_agent):

    # use pypi json to get python package version
    url = "https://pypi.org/pypi/%s/json" % source_app_name
    request_type = "get"

    # construct url for package details
    source_site_url = f"https://pypi.org/search/?q={source_app_name}"

    # download webpage content
    return_code, status_code, content = http_client(url=url, user_agent=user_agent, request_type=request_type)

    if return_code == 0:
        try:
            # decode json
            content = json.loads(content)

        except (ValueError, TypeError, KeyError, IndexError):
            app_logger_instance.info("Problem loading json from %s" % url)
            return None, source_site_url

    else:
        app_logger_instance.info("Problem downloading json content from %s" % url)
        return None, source_site_url

    try:
        current_version = content["info"]["version"]

    except (KeyError, TypeError):
        app_logger_instance.info("Problem extracting version from json for %s, skipping to next iteration..." % url)
        return None, source_site_url

    return current_version, source_site_url


def aor_apps(source_app_name, user_agent):

    # use aor unofficial api to get app release info
    url = "https://archlinux.org/packages/search/json/?q=%s" % source_app_name
    request_type = "get"

    # construct url for package details
    source_site_url = f"https://archlinux.org/packages/?sort=&q={source_app_name}&maintainer=&flagged="

    # download webpage content
    return_code, status_code, content = http_client(url=url, user_agent=user_agent, request_type=request_type)

    if return_code != 0:
        app_logger_instance.info("Problem downloading json content from %s" % url)
        return None, source_site_url

    try:
        # decode json
        content = json.loads(content)

        # filter python objects with list comprehension to prevent fuzzy mismatch
        content = [x for x in content["results"] if x["pkgname"] == source_app_name]

    except (ValueError, TypeError, KeyError, IndexError):
        app_logger_instance.info("Problem loading json from %s" % url)
        return None, source_site_url

    try:
        # get package version and release number from json
        pkgver = content[0]["pkgver"]
        pkgrel = content[0]["pkgrel"]

        # construct app version
        current_version = "%s-%s" % (pkgver, pkgrel)

    except (ValueError, TypeError, KeyError, IndexError):
        app_logger_instance.info("Problem parsing json from %s, skipping to next iteration..." % url)
        return None, source_site_url

    return current_version, source_site_url


def aur_apps(source_app_name, user_agent):

    # use aur api to get app release info
    url = "https://aur.archlinux.org/rpc/?v=5&type=info&arg[]=%s" % source_app_name
    request_type = "get"

    # construct url for package details
    source_site_url = "https://aur.archlinux.org/packages/%s/" % source_app_name

    # download webpage content
    return_code, status_code, content = http_client(url=url, user_agent=user_agent, request_type=request_type)

    if return_code == 0:
        try:
            content = json.loads(content)

        except (ValueError, TypeError, KeyError):
            app_logger_instance.info("Problem loading json from %s" % url)
            return None, source_site_url

    else:
        app_logger_instance.info("Problem downloading json content from %s" % url)
        return None, source_site_url

    try:
        # get app version from json
        current_version = content["results"][0]["Version"]

    except (IndexError, KeyError):
        app_logger_instance.info("Problem parsing json from %s, skipping to next iteration..." % url)
        return None, source_site_url

    return current_version, source_site_url


# NOTE: This function is named soup_regex for historical reasons but does not use regex —
# it returns a BeautifulSoup object from the HTML at the given URL.
def soup_regex(source_site_url, user_agent):

    # download webpage
    request_type = "get"

    # download webpage content
    return_code, status_code, content = http_client(
        url=source_site_url, user_agent=user_agent, request_type=request_type
    )

    if return_code == 0:
        try:
            soup = BeautifulSoup(content, features="html.parser")

        except (ValueError, TypeError, KeyError):
            app_logger_instance.info("Problem extracting url using regex from url  %s" % source_site_url)
            return None, None

    else:
        app_logger_instance.info("Problem downloading webpage from url  %s" % source_site_url)
        return None, None

    return soup, source_site_url


APP_DOWN_COUNTER_MAX = 3  # max consecutive failed-app-detection emails before suppressing


def _handle_app_fetch(current_version, site_key, source_site_name, source_app_name, source_repo_name, source_site_url):
    """Handle the common 'fetch succeeded or failed' pattern for a single app.

    On failure: increments the persistent counter, sends an app_error email if the
    count is at or below APP_DOWN_COUNTER_MAX, or suppresses notification.
    On success: resets this app's failure counter.

    Args:
        current_version: version returned by the site fetch, or None on failure
        site_key: "site:app" counter key
        source_site_name: name of the source site
        source_app_name: application name
        source_repo_name: repository name
        source_site_url: URL of the source site

    Returns:
        True if processing should continue (fetch succeeded), False to skip.
    """
    if current_version is None:
        _app_down_counters[site_key] = _app_down_counters.get(site_key, 0) + 1
        error_msg = (
            f"Unable to connect to site '{source_site_name}' "
            f"for application '{source_app_name}', skipping to next iteration..."
        )

        if _app_down_counters[site_key] <= APP_DOWN_COUNTER_MAX:
            notification_email(
                msg_type="app_error",
                error_msg=error_msg,
                source_site_name=source_site_name,
                source_repo_name=source_repo_name,
                source_app_name=source_app_name,
                source_site_url=source_site_url,
            )
        else:
            app_logger_instance.info(
                f"Number of failed downloads for site '{source_site_name}' "
                f"has exceeded '{APP_DOWN_COUNTER_MAX}', skipping notifications"
            )

        app_logger_instance.warning(error_msg)
        return False

    # Fetch succeeded — reset this app's counter
    _app_down_counters.pop(site_key, None)
    return True


def monitor_sites():

    # read sites list from config
    config_site_list = config_obj["monitor_sites"]["site_list"]
    target_repo_owner = config_obj["general"]["target_repo_owner"]

    # pretend to be windows 10 running chrome (required for minecraft bedrock)
    user_agent_chrome = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36"
    )

    # check github api is operational
    url = "https://api.github.com"

    site_down_github = check_site(url=url, user_agent=user_agent_chrome, site_name="GitHub")

    # check gitlab rest api is operational
    url = "https://gitlab.com/api/v4/projects"

    site_down_gitlab = check_site(url=url, user_agent=user_agent_chrome, site_name="GitLab")

    # check pypi website is operational
    test_package = "requests"
    url = f"https://pypi.org/pypi/{test_package}/json"

    site_down_pypi = check_site(url=url, user_agent=user_agent_chrome, site_name="PyPi")

    # check aor site is operational
    test_package = "base"
    url = f"https://archlinux.org/packages/core/any/{test_package}/"

    site_down_aor = check_site(url=url, user_agent=user_agent_chrome, site_name="AOR")

    # check aur site is operational
    test_package = "yay"
    url = f"https://aur.archlinux.org/rpc/?v=5&type=info&arg[]={test_package}"

    site_down_aur = check_site(url=url, user_agent=user_agent_chrome, site_name="AUR")

    # set counter for number of failures to get app package details
    # These are module-level dicts (_app_down_counters) so they persist across scheduler runs.
    # Counters are keyed by "site_name:app_name" so a success for one app does not reset the
    # counter for a different app on the same site that is still failing.

    # loop over each site and check previous and current result
    for site_item in config_site_list:
        source_site_name = site_item.get("source_site_name")
        source_app_name = site_item.get("source_app_name")
        source_repo_name = site_item.get("source_repo_name")
        source_project_id = site_item.get("source_project_id")
        source_branch_name = site_item.get("source_branch_name")
        target_release_days = site_item.get("target_release_days")
        target_repo_name = site_item.get("target_repo_name")
        target_repo_branch = site_item.get("target_repo_branch")
        source_query_type = site_item.get("source_query_type")
        grace_period_mins = site_item.get("grace_period_mins")
        source_version_change_datetime = site_item.get("source_version_change_datetime")
        action = site_item.get("action")

        # set default values in case they are not supplied
        source_site_url = None

        app_logger_instance.info("-------------------------------------")
        app_logger_instance.info("Processing started for application %s..." % source_app_name)

        if action != "notify":
            # if target branch not defined then send email notification and skip to next item
            if target_repo_branch is None:
                msg_type = "config_error"
                error_msg = (
                    "Target repo branch not defined for target repo '%s', skipping to next iteration..."
                    % target_repo_name
                )
                notification_email(
                    msg_type=msg_type,
                    error_msg=error_msg,
                    source_site_name=source_site_name,
                    source_repo_name=source_repo_name,
                    source_app_name=source_app_name,
                    source_site_url=source_site_url,
                )
                app_logger_instance.warning(error_msg)
                continue

        if source_site_name == "github":
            if site_down_github:
                app_logger_instance.warning(
                    "Site '%s' marked as down, skipping processing for application '%s'..."
                    % (source_site_name, source_app_name)
                )
                continue

            current_version, source_site_url = github_apps(
                source_app_name, source_query_type, source_repo_name, user_agent_chrome, source_branch_name
            )

            if not _handle_app_fetch(
                current_version,
                f"github:{source_app_name}",
                source_site_name,
                source_app_name,
                source_repo_name,
                source_site_url,
            ):
                continue

        elif source_site_name == "gitlab":
            if site_down_gitlab:
                app_logger_instance.warning(
                    "Site '%s' marked as down, skipping processing for application '%s'..."
                    % (source_site_name, source_app_name)
                )
                continue

            current_version, source_site_url = gitlab_apps(
                source_app_name,
                source_repo_name,
                source_project_id,
                source_branch_name,
                source_query_type,
                user_agent_chrome,
            )

            if not _handle_app_fetch(
                current_version,
                f"gitlab:{source_app_name}",
                source_site_name,
                source_app_name,
                source_repo_name,
                source_site_url,
            ):
                continue

        elif source_site_name == "pypi":
            if site_down_pypi:
                app_logger_instance.warning(
                    "Site '%s' marked as down, skipping processing for application '%s'..."
                    % (source_site_name, source_app_name)
                )
                continue

            current_version, source_site_url = pypi_apps(source_app_name, user_agent_chrome)

            if not _handle_app_fetch(
                current_version,
                f"pypi:{source_app_name}",
                source_site_name,
                source_app_name,
                source_repo_name,
                source_site_url,
            ):
                continue

        elif source_site_name == "aor":
            if site_down_aor:
                app_logger_instance.warning(
                    "Site '%s' marked as down, skipping processing for application '%s'..."
                    % (source_site_name, source_app_name)
                )
                continue

            # if grace period not defined then set to default value (required for aor)
            if grace_period_mins is None:
                grace_period_mins = 60

            current_version, source_site_url = aor_apps(source_app_name, user_agent_chrome)

            if not _handle_app_fetch(
                current_version,
                f"aor:{source_app_name}",
                source_site_name,
                source_app_name,
                source_repo_name,
                source_site_url,
            ):
                continue

        elif source_site_name == "aur":
            if site_down_aur:
                app_logger_instance.warning(
                    "Site '%s' marked as down, skipping processing for application '%s'..."
                    % (source_site_name, source_app_name)
                )
                continue

            current_version, source_site_url = aur_apps(source_app_name, user_agent_chrome)

            if not _handle_app_fetch(
                current_version,
                f"aur:{source_app_name}",
                source_site_name,
                source_app_name,
                source_repo_name,
                source_site_url,
            ):
                continue

        elif source_site_name == "regex":
            if source_app_name == "minecraftbedrock":
                request_type = "get"
                bedrock_unofficial_api = "https://net-secondary.web.minecraft-services.net/api/v1.0/download/links"
                return_code, status_code, content = http_client(
                    url=bedrock_unofficial_api, user_agent=user_agent_chrome, request_type=request_type
                )

                if return_code != 0:
                    msg_type = "app_error"
                    error_msg = (
                        "Unable to get bedrock download links from API for app %s, skipping to next iteration..."
                        % source_app_name
                    )
                    notification_email(
                        msg_type=msg_type,
                        error_msg=error_msg,
                        source_site_name=source_site_name,
                        source_repo_name=source_repo_name,
                        source_app_name=source_app_name,
                        source_site_url=source_site_url,
                    )
                    app_logger_instance.warning(error_msg)
                    continue

                else:
                    try:
                        # Parse the JSON response
                        api_data = json.loads(content)

                        # Find the serverBedrockLinux entry
                        download_url = None
                        for link_item in api_data["result"]["links"]:
                            if link_item["downloadType"] == "serverBedrockLinux":
                                download_url = link_item["downloadUrl"]
                                break

                        if not download_url:
                            raise KeyError("serverBedrockLinux not found in API response")

                        # Extract version from the download URL using regex
                        # URL format: https://www.minecraft.net/bedrockdedicatedserver/bin-linux/bedrock-server-1.21.90.4.zip
                        version_match = re.search(r"bedrock-server-(.*)\.zip", download_url)
                        if version_match:
                            current_version = version_match.group(1)
                        else:
                            raise ValueError("Could not extract version from download URL")

                    except (json.JSONDecodeError, KeyError, ValueError) as e:
                        msg_type = "app_error"
                        error_msg = "Unable to parse bedrock API response for app %s: %s" % (source_app_name, str(e))
                        notification_email(
                            msg_type=msg_type,
                            error_msg=error_msg,
                            source_site_name=source_site_name,
                            source_repo_name=source_repo_name,
                            source_app_name=source_app_name,
                            source_site_url=source_site_url,
                        )
                        app_logger_instance.warning(error_msg)
                        continue

            elif source_app_name == "minecraftserver":
                request_type = "get"
                source_site_url = "https://launchermeta.mojang.com/mc/game/version_manifest.json"

                # get version manifest content
                return_code, status_code, content = http_client(
                    url=source_site_url, user_agent=user_agent_chrome, request_type=request_type
                )

                if return_code != 0:
                    msg_type = "app_error"
                    error_msg = (
                        "Problem downloading version manifest for url '%s', skipping to next iteration..."
                        % source_site_url
                    )
                    notification_email(
                        msg_type=msg_type,
                        error_msg=error_msg,
                        source_site_name=source_site_name,
                        source_repo_name=source_repo_name,
                        source_app_name=source_app_name,
                        source_site_url=source_site_url,
                    )
                    app_logger_instance.warning(error_msg)
                    continue

                version_manifest_content = None
                try:
                    version_manifest_content = json.loads(content)

                except (json.JSONDecodeError, ValueError):
                    msg_type = "app_error"
                    error_msg = (
                        "Unable to decode version manifest json for app '%s', skipping to next iteration..."
                        % source_app_name
                    )
                    notification_email(
                        msg_type=msg_type,
                        error_msg=error_msg,
                        source_site_name=source_site_name,
                        source_repo_name=source_repo_name,
                        source_app_name=source_app_name,
                        source_site_url=source_site_url,
                    )
                    app_logger_instance.warning(error_msg)
                    continue

                try:
                    current_version = version_manifest_content["latest"]["release"]

                except (IndexError, KeyError):
                    msg_type = "app_error"
                    error_msg = "Unable to identify current release version for app '%s', ignoring..." % source_app_name
                    notification_email(
                        msg_type=msg_type,
                        error_msg=error_msg,
                        source_site_name=source_site_name,
                        source_repo_name=source_repo_name,
                        source_app_name=source_app_name,
                        source_site_url=source_site_url,
                    )
                    app_logger_instance.warning(error_msg)
                    continue

            else:
                app_logger_instance.warning(
                    "Source site app %s unknown, skipping to next iteration..." % source_app_name
                )
                continue

        else:
            app_logger_instance.warning("Source site name %s unknown, skipping to next iteration..." % source_site_name)
            continue

        # write value for current match to config
        config_obj["results"]["%s_%s_%s_current_version" % (source_site_name, source_app_name, target_repo_name)] = (
            current_version
        )
        config_obj.write()

        try:
            # read value from previous match from config
            previous_version = config_obj["results"][
                "%s_%s_%s_previous_version" % (source_site_name, source_app_name, target_repo_name)
            ]

        except KeyError:
            app_logger_instance.info("No known previous version for app %s, assuming first run" % source_app_name)
            app_logger_instance.info(
                "Setting previous version to current version %s and going to next iteration" % current_version
            )
            config_obj["results"][
                "%s_%s_%s_previous_version" % (source_site_name, source_app_name, target_repo_name)
            ] = current_version
            config_obj.write()
            continue

        if previous_version != current_version:
            if action == "trigger":
                current_datetime_object = datetime.datetime.now()
                current_datetime_str = current_datetime_object.strftime("%Y-%m-%d %H:%M:%S")

                if grace_period_mins:
                    if source_version_change_datetime is None:
                        app_logger_instance.debug(
                            "Trigger datetime not defined in config.ini, creating from current datetime"
                        )
                        source_version_change_datetime = current_datetime_str

                        site_item["source_version_change_datetime"] = source_version_change_datetime
                        config_obj.write()
                        continue

                    # run function to check if time since last source change is greater than or equal to grace period
                    else:
                        source_version_change_datetime_object = datetime.datetime.strptime(
                            source_version_change_datetime, "%Y-%m-%d %H:%M:%S"
                        )

                        if not time_check(
                            current_datetime_object, grace_period_mins, source_version_change_datetime_object
                        ):
                            app_logger_instance.info(
                                "Source version change for app '%s' is less than '%s' mins ago, "
                                "skipping to next iteration..." % (source_app_name, grace_period_mins)
                            )
                            continue

                        else:
                            app_logger_instance.info(
                                "Source version change for app '%s' is >= '%s' mins ago, proceeding..."
                                % (source_app_name, grace_period_mins)
                            )

                if target_release_days:
                    return_code, last_release_date = github_target_last_release_date(
                        target_repo_owner, target_repo_name, user_agent_chrome
                    )

                    if return_code != 0:
                        app_logger_instance.warning(
                            "Unable to identify target release date for repo '%s', skipping to next iteration..."
                            % target_repo_name
                        )
                        continue

                    target_release_date_object = datetime.datetime.strptime(last_release_date, "%Y-%m-%dT%H:%M:%SZ")

                    # compare difference between local date/time and trigger date/time to produce timedelta
                    target_time_delta = current_datetime_object - target_release_date_object

                    # extract days from time delta
                    target_time_delta_days = target_time_delta.days
                    app_logger_instance.debug("Minimum days between target releases is '%s' days" % target_release_days)
                    app_logger_instance.debug("Last target release was '%s' days ago" % target_time_delta_days)

                    if int(target_time_delta_days) >= int(target_release_days):
                        app_logger_instance.info(
                            "Last target release date for app '%s' is >= '%s' days ago, proceeding..."
                            % (target_repo_name, target_release_days)
                        )

                    else:
                        app_logger_instance.info(
                            "Last target release date for app '%s' is less than '%s' days ago, "
                            "skipping to next iteration..." % (target_repo_name, target_release_days)
                        )
                        continue

                app_logger_instance.info(
                    "Previous version %s and current version %s are different, "
                    "triggering a docker hub build (via github tag)..." % (previous_version, current_version)
                )
                return_code, status_code, content = github_create_release(
                    current_version, target_repo_branch, target_repo_owner, target_repo_name, user_agent_chrome
                )

                if status_code == 201:
                    app_logger_instance.info(
                        "Setting previous version %s to the same as current version %s after successful build"
                        % (previous_version, current_version)
                    )

                else:
                    # TODO this is a hack to work around the fact we have converted dict to keyword args
                    regex_code = r'(?<="code":\s")[^"]+'

                    try:
                        code = (re.search(regex_code, str(content))).group(0)
                        if code.lower() == "already_exists":
                            app_logger_instance.warning(
                                "Problem creating GitHub release as it already exists for '%s/%s', "
                                "overwriting current version and skipping to next iteration..."
                                % (target_repo_owner, target_repo_name)
                            )
                            app_logger_instance.debug("Writing current version %s to config.ini" % current_version)
                            config_obj["results"][
                                "%s_%s_%s_previous_version" % (source_site_name, source_app_name, target_repo_name)
                            ] = current_version
                            config_obj.write()

                    except AttributeError:
                        app_logger_instance.warning(
                            "Problem creating GitHub release due to unknown error for '%s/%s', "
                            "skipping to next iteration..." % (target_repo_owner, target_repo_name)
                        )

                    continue

                if source_version_change_datetime is not None:
                    app_logger_instance.debug(
                        "Deleting 'source_version_change_datetime', used next time version change occurs"
                    )
                    del site_item["source_version_change_datetime"]
                    config_obj.write()

                app_logger_instance.debug(
                    "Creating 'target_trigger_datetime', used to track when trigger of docker build happened"
                )
                site_item["target_trigger_datetime"] = current_datetime_str
                config_obj.write()

            elif action == "notify":
                app_logger_instance.info(
                    "Previous version %s and current version %s are different" % (previous_version, current_version)
                )

            app_logger_instance.debug("Writing current version %s to config.ini" % current_version)
            config_obj["results"][
                "%s_%s_%s_previous_version" % (source_site_name, source_app_name, target_repo_name)
            ] = current_version
            config_obj.write()

            notification_email(
                action=action,
                source_app_name=source_app_name,
                source_repo_name=source_repo_name,
                source_site_name=source_site_name,
                source_site_url=source_site_url,
                target_repo_name=target_repo_name,
                previous_version=previous_version,
                current_version=current_version,
            )

            notification_kodi(action, source_app_name, current_version)

        else:
            app_logger_instance.info(
                "Previous version %s and current version %s match, nothing to do" % (previous_version, current_version)
            )

        app_logger_instance.info("Processing finished for application %s" % source_app_name)

    # write timestamp to config.ini
    config_obj["general"]["last_check"] = time.strftime("%c")
    config_obj.write()


def ondemand_start():

    app_logger_instance.info("Checking for version changes...")
    monitor_sites()


def scheduler_start():

    schedule_check_mins = config_obj["general"]["schedule_check_mins"]

    # now run monitor_sites function via scheduler
    schedule.every(schedule_check_mins).minutes.do(monitor_sites)

    while True:
        try:
            schedule.run_pending()
            # Sleep most of the interval to avoid flooding the log.
            # Wake every 30s to catch clock drift; schedule.run_pending() is cheap.
            sleep_secs = 30
            time.sleep(sleep_secs)

        except KeyboardInterrupt:
            app_logger_instance.info("Keyboard interrupt received, exiting script...")
            sys.exit()

        except Exception as e:
            app_logger_instance.warning("Unhandled exception in scheduler loop: %s" % e)
            app_logger_instance.debug("Exception details:", exc_info=True)


# required to prevent separate process from trying to load parent process
if __name__ == "__main__":
    version = "1.2.4"

    # custom argparse to redirect user to help if unknown argument specified
    class ArgparseCustom(argparse.ArgumentParser):
        def error(self, message):
            sys.stderr.write("error: %s\n" % message)
            self.print_help()
            sys.exit(2)

    # setup argparse description and usage, also increase spacing for help to 50
    commandline_parser = ArgparseCustom(
        prog="TriggerDockerBuild",
        description="%(prog)s " + version,
        usage=(
            "%(prog)s [--help] [--config <path>] [--logs <path>] [--kodi-password <password>] "
            "[--email-to <email address>] [--email-username <username>] "
            "[--email-password <password>] [--target-access-token <token>] [--pidfile <path>] "
            "[--kodi-notification] [--email-notification] [--schedule] [--daemon] [--version]"
        ),
        formatter_class=lambda prog: argparse.HelpFormatter(prog, max_help_position=50),
    )

    # add argparse command line flags
    commandline_parser.add_argument(
        "--config", metavar="<path>", help="specify path for config file e.g. --config /opt/triggerdockerbuild/config/"
    )
    commandline_parser.add_argument(
        "--logs", metavar="<path>", help="specify path for log files e.g. --logs /opt/triggerdockerbuild/logs/"
    )
    commandline_parser.add_argument(
        "--kodi-password", metavar="<password>", help="specify the password to access kodi e.g. --kodi-password foo"
    )
    commandline_parser.add_argument(
        "--email-to",
        metavar="<email address>",
        help="specify the email address to send email notifications to e.g. --email-to foo@bar.com",
    )
    commandline_parser.add_argument(
        "--email-username",
        metavar="<username>",
        help="specify the email account username e.g. --email-username foo@bar.com",
    )
    commandline_parser.add_argument(
        "--email-password", metavar="<password>", help="specify the email account password e.g. --email-password foo"
    )
    commandline_parser.add_argument(
        "--target-access-token",
        metavar="<token>",
        help="specify the github personal access token e.g. --target-access-token 123456789",
    )
    commandline_parser.add_argument(
        "--kodi-notification", action="store_true", help="enable kodi notification e.g. --kodi-notification"
    )
    commandline_parser.add_argument(
        "--email-notification", action="store_true", help="enable email notification e.g. --email-notification"
    )
    commandline_parser.add_argument(
        "--pidfile",
        metavar="<path>",
        help="specify path to pidfile e.g. --pid /var/run/triggerdockerbuild/triggerdockerbuild.pid",
    )
    commandline_parser.add_argument("--schedule", action="store_true", help="enable scheduling e.g. --schedule")
    commandline_parser.add_argument("--daemon", action="store_true", help="run as daemonized process e.g. --daemon")
    commandline_parser.add_argument("--version", action="version", version=version)

    # save arguments in dictionary
    args = vars(commandline_parser.parse_args())

    # set path to root folder for application
    app_root_dir = os.path.dirname(os.path.realpath(__file__))

    if not args["config"]:
        # set folder path for config files
        config_dir = os.path.join(app_root_dir, "configs")
        config_dir = os.path.normpath(config_dir)

    else:
        config_dir = args["config"]

    # set path for config.ini file
    config_ini = os.path.join(config_dir, "config.ini")

    # create config and logs paths if they dont exist
    if not os.path.exists(config_dir):
        os.makedirs(config_dir)

    # set path for configspec.ini file
    configspec_ini = os.path.join(app_root_dir, "configs/configspec.ini")

    # create configobj instance, set config.ini file, set encoding and set configspec.ini file
    config_obj = configobj.ConfigObj(
        config_ini,
        list_values=False,
        write_empty_values=True,
        encoding="UTF-8",
        default_encoding="UTF-8",
        configspec=configspec_ini,
        unrepr=True,
    )

    # create config.ini
    create_config()

    if not args["logs"]:
        # set folder path for log files
        logs_dir = os.path.join(app_root_dir, "logs")
        logs_dir = os.path.normpath(logs_dir)

    else:
        logs_dir = args["logs"]

    # set path for log file
    app_log_file = os.path.join(logs_dir, "app.log")

    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)

    # setup logging
    app_log = app_logging()
    app_logger_instance = app_log.get("logger")
    app_handler = app_log.get("handler")

    if args["email_notification"]:
        email_notification = args["email_notification"]

    elif config_obj["notification"]["email_notification"] is not None:
        email_notification = config_obj["notification"]["email_notification"]

    else:
        app_logger_instance.info(
            "Email Notification is not defined via '--email-notification' or 'config.ini', defaulting to False"
        )
        email_notification = False

    if not email_notification:
        app_logger_instance.info("Email notification is disabled")

    if args["email_to"]:
        email_to = args["email_to"]

    elif config_obj["notification"]["email_to"] is not None:
        email_to = config_obj["notification"]["email_to"]

    else:
        app_logger_instance.info(
            "Email To is not defined via '--email-to' or 'config.ini', setting Email Notification to false"
        )
        email_notification = False

    if args["email_username"]:
        email_username = args["email_username"]

    elif config_obj["notification"]["email_username"] is not None:
        email_username = config_obj["notification"]["email_username"]

    else:
        app_logger_instance.info(
            "Email Username is not defined via '--email-username' or 'config.ini', setting Email Notification to false"
        )
        email_notification = False

    if args["email_password"]:
        email_password = args["email_password"]

    elif config_obj["notification"]["email_password"] is not None:
        email_password = config_obj["notification"]["email_password"]

    else:
        app_logger_instance.info(
            "Email Password  is not defined via '--email-password' or 'config.ini', setting Email Notification to false"
        )
        email_notification = False

    if args["kodi_notification"]:
        kodi_notification = args["kodi_notification"]

    elif config_obj["notification"]["kodi_notification"] is not None:
        kodi_notification = config_obj["notification"]["kodi_notification"]

    else:
        app_logger_instance.info(
            "Kodi Notification is not defined via '--kodi-password' or 'config.ini', setting Kodi Notification to false"
        )
        kodi_notification = False

    if args["kodi_password"]:
        kodi_password = args["kodi_password"]

    elif config_obj["notification"]["kodi_password"] is not None:
        kodi_password = config_obj["notification"]["kodi_password"]

    else:
        app_logger_instance.info(
            "Kodi Notification is not defined via '--kodi-password' or 'config.ini', setting Kodi Notification to false"
        )
        kodi_notification = False

    if args["target_access_token"]:
        target_access_token = args["target_access_token"]

    elif config_obj["general"]["target_access_token"] is not None:
        target_access_token = config_obj["general"]["target_access_token"]

    else:
        app_logger_instance.warning(
            "Target Access Token is not defined via '--target-access-token' or 'config.ini', exiting script..."
        )
        exit(1)

    # Verify TLS certificates by default; allow opt-out for SSL-inspection environments.
    # The configspec defines boolean(default=True), so the key is always present.
    verify_ssl = config_obj["general"]["verify_ssl"]

    # Silence urllib3 InsecureRequestWarning only when verification is disabled
    _silence_tls_warnings(verify_ssl)

    # check os is not windows and then run main process as daemonized process
    if args["daemon"] is True and os.name != "nt":
        app_logger_instance.info("Running as a daemonized process...")

        # specify the logging handler as an exclusion to the daemon, to prevent its output being closed
        daemon_context = daemon.DaemonContext()
        daemon_context.files_preserve = [app_handler.stream]
        daemon_context.open()

    else:
        app_logger_instance.info("Running as a foreground process...")

    if args["schedule"] is True:
        app_logger_instance.info("Running via schedule...")
        scheduler_start()

    else:
        app_logger_instance.info("Running on demand...")
        ondemand_start()
