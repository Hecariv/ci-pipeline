import argparse
import copy
import os
import sys

import yaml
import logging
import calmpy
import textwrap

from calmpy import ProjectNotFound

ID = "id"
NAME = "name"
TRACKERS = "trackers"

SOURCE = "source"
SOURCE_PROJECT = "source_project"
SOURCE_TRACKER = "source_tracker"

error_counter = 0

calmpy.enable_logging(log_level=logging.INFO)
logger = logging.getLogger(__name__)
#   Set log level for custom logger to debug (default is warning)
logger.setLevel("DEBUG")
#   reuse formatter and handler from calmpy to get fancy formatted output
logger.handlers = logging.getLogger("calmpy").handlers


class CreateUpdateProjectFailedException(Exception):
    pass


def get_args():
    """get the arguments of the script"""

    logger.debug("Reading arguments from commandline")

    parser = argparse.ArgumentParser(
        description="this script creates/updates a tracker collection via inheritance from a given config file",
    )

    parser.add_argument(
        "-s",
        "--server",
        action="store",
        required=True,
        help="codebeamer server (short or URL possible)",
    )
    parser.add_argument(
        "-cfg",
        "--config_files",
        action="store",
        required=True,
        help="config filesProject-A.1.config.jsonProject-A.1.config.json (JSON)",
        nargs="*",
    )
    parser.add_argument(
        "-u",
        "--username",
        action="store",
        default=r"bond",
        help="username",
    )

    parser.add_argument(
        "-pw",
        "--password",
        action="store",
        default=r"007",
        help="password",
    )

    args = parser.parse_args()

    logger.info("Running with args:\n" + str(args))
    return args


def update_tracker_from_config(cb_server, cb_project, config_dict):
    global error_counter
    logger.info(
        "\t Checking for additional trackers in project against configuration file"
    )
    missing_config_blocks = server_vs_config(
        cb_server, cb_project, config_dict
    )  # differences of server implementation opposite to config file
    logger.info(
        "\t Checking for additional trackers in configuration file against project"
    )
    config_vs_server(
        cb_server, cb_project, config_dict
    )  # differences of config file opposite to server configuration

    if len(missing_config_blocks) > 0:
        logger.error(
            "The project contains a tracker which is not mentioned in your config file.\n"
            " To remove the warning, add the following to your configuration file."
        )
        missing_config_blocks.insert(0, "\n")
        missing_config_blocks.insert(1, "".join(["#"] * 100))
        missing_config_blocks.append("".join(["#"] * 100))
        logger.error("\n".join(missing_config_blocks))
        error_counter += 1


def server_vs_config(cb_server: calmpy.Server, cb_project: calmpy.Project, config_dict):
    """
    Check if server configuration of trackers has differences to the configuration file and applies the changes.
    :param cb_server:
    :param cb_project: project in codebeamer
    :param config_dict: configuration file
    """

    config_tracker_names = [t[NAME] for t in config_dict[TRACKERS] if NAME in t]
    config_tracker_ids = [t[ID] for t in config_dict[TRACKERS] if ID in t]

    trackers_on_server = cb_project.get_trackers()

    missing_config_blocks = []

    for server_tracker in trackers_on_server:
        if server_tracker.name not in config_tracker_names:
            server_tracker_config = cb_server.query.get_tracker_api(
                tracker=server_tracker.id
            )  # for update

            if (
                    server_tracker.id not in config_tracker_ids
                    and not server_tracker_config["hidden"]
            ):
                template_tracker = cb_server.get_tracker(
                    server_tracker_config["templateTracker"][ID]
                )
                missing_config_blocks.append(
                    textwrap.dedent(
                        f"""
                        - name: {server_tracker.name}
                          id: {server_tracker.id}
                          source:
                            source_project: {template_tracker.project[NAME]}
                            source_tracker: {template_tracker.name}
                        """
                    )
                )

            # tracker on server exists and the name is not in the config, but the id -> rename
            elif server_tracker.id in config_tracker_ids:
                new_name = [
                    t_[NAME]
                    for t_ in config_dict[TRACKERS]
                    if ID in t_ and t_[ID] == server_tracker.id
                ][0]
                server_tracker.update_tracker_config(
                    {
                        NAME: new_name,
                    }
                )
    return missing_config_blocks


def config_vs_server(
        cb_server: calmpy.Server, cb_project: calmpy.Project, config_dict: dict
):
    """
    Check if configuration file has changes to the configuration of the server and applies the changes.
    :param cb_server:
    :param cb_project: Codebeamer project
    :param config_dict: configuration dict
    """
    global error_counter
    server_trackers = cb_project.get_trackers()

    existing_tracker_names = [t.name for t in server_trackers]
    collected_projects = {}
    collected_tracker = {}

    for config_tracker in config_dict[TRACKERS]:

        source_project_name = None
        if SOURCE in config_tracker:
            source_tracker_name = config_tracker[SOURCE][SOURCE_TRACKER]

            if SOURCE_PROJECT in config_tracker[SOURCE]:
                source_project_name = config_tracker[SOURCE][SOURCE_PROJECT]
        else:
            logger.error(
                f"Tracker must inheritance from another tracker. "
                f"Add a source for tracker:{config_tracker[NAME]} in your config."
            )
            error_counter += 1
            break

        # use project caching
        if source_project_name:
            if source_project_name not in collected_projects.keys():
                source_project = cb_server.get_project(project=source_project_name)
                collected_projects[source_project_name] = source_project
            else:
                source_project = collected_projects[source_project_name]
        else:
            source_project = None

        # use tracker caching
        if source_tracker_name not in collected_tracker.keys():
            if source_project:
                source_tracker = source_project.get_tracker(tracker=source_tracker_name)
            else:
                source_tracker = cb_project.get_tracker(
                    tracker=source_tracker_name
                )  # tracker must already exist here
            collected_tracker[source_tracker_name] = source_tracker
        else:
            source_tracker = collected_tracker[source_tracker_name]

        # tracker exists in config but not in codebeamer
        if config_tracker[NAME] not in existing_tracker_names:
            if ID not in config_tracker:
                # create tracker
                if "key" in config_tracker:
                    key = config_tracker["key"]
                else:
                    key = source_tracker.keyName  # noqa

                new_tracker = cb_project.create_tracker(
                    name=config_tracker[NAME],
                    key=key,
                    tracker_type="",
                    template_id=source_tracker.id,
                )
                logger.info(f"Created tracker {new_tracker.name}.")
            else:
                tracker_id = config_tracker[ID]
                given_tracker = cb_server.get_tracker(tracker_id)
                if given_tracker.project[ID] != cb_project.id:
                    logger.error(
                        f"Tracker with id: {tracker_id} does not exists in project with id: {cb_project.id}.\n"
                        f"Project and Tracker id's must match."
                    )
                    error_counter += 1
                else:
                    check_inheritance(cb_server, source_tracker.id, tracker_id)

                    given_tracker.update_tracker_config({NAME: config_tracker[NAME]})

        elif config_tracker[NAME] in existing_tracker_names:
            affected_tracker = [
                cb_t for cb_t in server_trackers if cb_t.name == config_tracker[NAME]
            ][0]
            check_inheritance(cb_server, source_tracker.id, affected_tracker.id)


def read_allow_upstream(config_tracker: dict):
    """
    reading to allow upstream parameter from the config
    :param config_tracker: configuration of a tracker
    :return: bool
    """
    allow_upstream = False

    if "allow_upstream_to_other_project" in config_tracker:
        allow_upstream = config_tracker["allow_upstream_to_other_project"]

    return allow_upstream


def check_inheritance(
        cb_server: calmpy.Server, source_tracker_id: int, tracker_id: int
):
    """Checking for invalid references
    Checks if the inheritance is not broken.
    :param cb_server: Codebeamer Server
    :param source_tracker_id: Source tracker of config
    :param tracker_id: tracker id on codebeamer
    """
    global error_counter
    cbt_config = cb_server.query.get_tracker_api(tracker=tracker_id)
    if (
            NAME in cbt_config["templateTracker"]
            and not cbt_config["templateTracker"][NAME] == source_tracker_id
    ):
        logger.error(
            f"Tracker inheritance for tracker {cbt_config[NAME]} cannot be changed without data loss.\n"
            f"Revert to original tracker {cbt_config['templateTracker'][NAME]} "
            f"or create a new tracker. (Data migration might be needed)"
        )
        error_counter += 1


def check_fields_config(cb_project: calmpy.Project, config: dict = False):
    """
    Check if the reference fields in the codebeamer project are valid.
    :param config: configuration as a dict
    :param cb_project: Codebeamer Projekt
    """
    global error_counter
    for p_tracker in cb_project.get_trackers():
        config_tracker = [
            t_c for t_c in config["trackers"] if t_c[NAME] == p_tracker.name
        ]
        if len(config_tracker) == 1:
            allow_upstream = read_allow_upstream(config_tracker[0])
            if not allow_upstream:
                logger.info(
                    f"Checking upstream references for tracker '{p_tracker.name}' ..."
                )
                affected_fields = check_references(cb_project, p_tracker)

                if affected_fields:
                    logger.error(
                        f"Found {len(affected_fields)} invalid field configurations in tracker {p_tracker.name}"
                    )
                    for f in affected_fields:
                        logger.error(
                            f"Field {f['label']} has references to other "
                            f"projects {','.join([f_['name'] for f_ in f['trackerFilter']])}"
                        )
                        error_counter += 1
            else:
                logger.info(
                    f"For tracker '{p_tracker.name}' upstream references to other projects are allowed -> no "
                    f"check here."
                )


def check_references(cb_project: calmpy.Project, p_tracker: calmpy.Tracker) -> []:
    """
    Check if the configuration of the fields is correct
    :param cb_project:
    :param p_tracker:
    :return: fields which must be changed
    """
    affected_fields = []
    field_configurations = p_tracker.get_field_config()

    for field_config in field_configurations:
        if (
                hasattr(field_config, "refType")
                and hasattr(field_config, "trackerFilter")
                and field_config.refType == "Work_Config_Items"
        ):
            for tracker_filter in field_config.trackerFilter:
                if cb_project.name != tracker_filter[NAME]:
                    affected_fields.append(field_config)

    return affected_fields


def create_update_codebeamer_project_from_config(
        server: str, config_file: str, username: str = "bond", password: str = "007"
):
    global error_counter
    # read JSON config
    with open(config_file) as f:
        config_dict = yaml.safe_load(f)

    empty_cb_project_zip = f"{os.path.dirname(os.path.realpath(__file__))}/resources/CARIAD_Empty_Template.zip"

    # read project from config
    project_name = config_dict["project"]

    cb_server = calmpy.Server(
        url=server, user=username, password=password, readonly=False
    )

    if "category" in config_dict:
        project_category = config_dict["category"]
        categories_on_server = cb_server.query.get_project_categories_api()
        categories_names = [c["categoryName"] for c in categories_on_server]
        if project_category not in categories_names:
            raise ValueError(
                f"Given category '{project_category}' does not exist on server."
            )
    else:
        project_category = None
        logger.warning(
            "Project has no category. Please add a category in the configuration file."
        )

    # create project if not exist
    try:
        cb_project = cb_server.get_project(project=project_name)
    except ProjectNotFound:
        cb_project = cb_server.create_project_from_zip(
            project_name=project_name,
            key_name="",
            file=empty_cb_project_zip,
            category=project_category,
        )
        data_template = cb_project.export_as_template()
        data_base = copy.deepcopy(data_template)  #
        cb_project.upload_documents(data_base, filename="template.zip")
        cb_project.upload_documents(data_template, filename="projectBase.zip")

    logger.info("Applying configuration to codebeamer...")
    update_tracker_from_config(cb_server, cb_project, config_dict)
    logger.info("Checking for invalid references...")
    check_fields_config(cb_project, config_dict)

    if error_counter > 0:
        msg = f"Found {error_counter} errors. Check the logs for more details."
        logger.error(msg)
        error_counter = 0
        raise CreateUpdateProjectFailedException(msg)


def main():

    args = get_args()
    server = args.server
    username = args.username
    password = args.password
    config_files = args.config_files

    for config_file in config_files:
        create_update_codebeamer_project_from_config(
            server=server, config_file=config_file, username=username, password=password
        )


if __name__ == "__main__":
    try:
        main()
    except CreateUpdateProjectFailedException as e:
        sys.exit(1)
    logger.info("DONE")