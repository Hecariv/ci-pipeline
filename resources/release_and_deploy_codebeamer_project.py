import argparse
import logging
import os
import tempfile
import typing
from pathlib import Path

import yaml
import alm8utils

import calmpy

calmpy.enable_logging(log_level=logging.INFO)
logger = logging.getLogger(__name__)
#   Set log level for custom logger to debug (default is warning)
logger.setLevel("DEBUG")
#   reuse formatter and handler from calmpy to get fancy formatted output
logger.handlers = logging.getLogger("calmpy").handlers

timestamp = alm8utils.get_timestamp()

def get_args():
    """get the arguments of the batch file"""

    logger.debug("read arguments from commandline")

    parser = argparse.ArgumentParser(
        description="this script creates a baseline for a given project on a given server. Export the entire project "
                    "as template and for deployment and upload the needed files into the documents area of the "
                    "project in codebeamer",
        epilog="this script creates a baseline for a given project on a given server. Export the entire project as "
               "template and for deployment and upload the needed files into the documents area of the project in "
               "codebeamer",
    )

    parser.add_argument(
        "-s",
        "--source_server",
        action="store",
        required=True,
        help="codebeamer source server (short or URL possible)",
    )

    parser.add_argument(
        "-t",
        "--target_server",
        action="store",
        required=True,
        help="codebeamer target server (short or URL possible)",
    )

    parser.add_argument(
        "-d",
        "--deploy_project",
        action="store_true",
        default=True,
        help="deploy project",
    )

    parser.add_argument(
        "-dd",
        "--deploy_dependencies",
        action="store_true",
        default=False,
        help="deploy project with dependencies (FullDeployment vs SelectiveDeployment",
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

    parser.add_argument(
        "-c",
        "--config",
        action="store",
        required=True,
        help="file path of the YAML for the project",
    )

    args = parser.parse_args()

    logger.info("Running with args:\n" + str(args))
    return args


def main():
    args = get_args()
    source_server = args.source_server
    target_server = args.target_server
    yaml_path = args.config
    username = args.username
    password = args.password
    deploy_project = args.deploy_project

    release_and_deploy_codebeamer_project(
        source=source_server,
        target=target_server,
        yaml_path=yaml_path,
        username=username,
        password=password,
    )

    logger.info("Done")


def get_yaml_to_project_name_mapping(directory: str):
    project_name_to_yaml_path = {}
    for path in Path(directory).rglob("release.y*ml"):
        yaml_path = os.path.join(path.parent, path.name)
        logger.info(f"found yaml file: {yaml_path}")
        yaml_config = yaml.safe_load(open(yaml_path, "r"))
        project_name_to_yaml_path[yaml_config["project"]] = yaml_path

    if len(project_name_to_yaml_path) == 0:
        raise ValueError(f"no release.y*ml files found in directory {directory}")
    logger.info(project_name_to_yaml_path)
    return project_name_to_yaml_path


def release_and_deploy_full_tc_codebeamer_project(
        source: str, target: str, yaml_config: typing.Dict, username: str, password: str
):
    full_tc_project_name = yaml_config["project"]
    logger.info(f"running full deployment for project {full_tc_project_name}")

    project_name_to_yaml_path = get_yaml_to_project_name_mapping(os.getcwd())

    source_server = calmpy.Server(
        url=source, user=username, password=password, readonly=False
    )
    target_server = calmpy.Server(
        url=target, user=username, password=password, readonly=False
    )
    full_tc_project = source_server.get_project(full_tc_project_name)
    # get deploy dependencies
    logger.info("reading dependencies for project")
    project_dependencies = (
        full_tc_project._get_project_settings_dependencies_for_deployment()
    )

    baseline_suffix = " - TC Bundle"

    projects_needs_manual_baseline_after_deployment = []

    # iterating over dependent projects and run release and deploy without deploy step -> manual create baseline before deployment

    logger.info("iterating over dependent projects and run release and deploy without deploy step -> manual create baseline before deployment")
    for dep_project in project_dependencies:
        # if not our own project, run single release script with overwritten baseline and no deploy
        if not dep_project["projectId"] == full_tc_project.id:
            source_current_project = source_server.get_project(dep_project["projectId"])
            try:
                yaml_path_for_current_project = project_name_to_yaml_path[
                    source_current_project.name
                ]
            except KeyError as e:
                logger.error(f"no yaml file for dependent project {source_current_project.name} found")
                raise e
            logger.info(f"dependent project: {source_current_project.name}")
            with open(yaml_path_for_current_project, "r") as stream:
                try:
                    yaml_config_for_current_project = yaml.safe_load(stream)
                    baseline_name = f"{yaml_config['project_version']}{baseline_suffix}"
                    logger.info(f"overwrite baseline: {baseline_name}")
                    yaml_config_for_current_project["project_version"] = baseline_name
                    # FIXME: currently deploy_project False prevents from creating baselines on target server
                    release_and_deploy_single_codebeamer_project(
                        source,
                        target,
                        yaml_config_for_current_project,
                        username,
                        password,
                        deploy_project=False,
                    )

                    projects_needs_manual_baseline_after_deployment.append(yaml_config_for_current_project)

                    # create before deployment baseline on target server
                    try:
                        target_current_project = target_server.get_project(source_current_project.name)
                    except calmpy.ProjectNotFound:
                        target_current_project = None
                    if target_current_project is not None:
                        _create_baseline(
                            cb_project=target_current_project,
                            yaml_config=yaml_config_for_current_project,
                            baseline_suffix="pre deployment",
                            description="baseline before deployment",
                        )
                except yaml.YAMLError as exc:
                    logger.exception(exc)

    # run for own project release and deploy
    logger.info("run release and deploy Full")
    baseline_name = f"{yaml_config['project_version']}{baseline_suffix}"
    yaml_config["project_version"] = baseline_name
    release_and_deploy_single_codebeamer_project(
        source, target, yaml_config, username, password
    )

    # create after full deployment baseline
    for yaml_config_dep_project in projects_needs_manual_baseline_after_deployment:
        target_server.projects = None
        target_current_project = target_server.get_project(yaml_config_dep_project["project"])
        _create_baseline(
            cb_project=target_current_project,
            yaml_config=yaml_config_dep_project,
            baseline_suffix="post deployment",
            description="baseline after deployment",
        )



def release_and_deploy_codebeamer_project(
        source: str,
        target: str,
        yaml_path: str,
        username: str = "bond",
        password: str = "007",
):

    #     :param baseline_name: suffix of the baseline which is created in source and target project.
    #     If not given, the baseline will be in source: <project_version_from_yaml>_pre/post_release
    #     in target: <project_version_from_yaml>_pre/post_deploy
    #       If given, the baseline will be in source: <baseline_name>_pre/post_release
    #       in target: <baseline_name>_pre/post_deploy

    with open(yaml_path, "r") as stream:
        try:
            yaml_config = yaml.safe_load(stream)
            if "deploy_dependencies" in yaml_config:
                release_and_deploy_full_tc_codebeamer_project(
                    source=source,
                    target=target,
                    yaml_config=yaml_config,
                    username=username,
                    password=password,
                )
            else:
                release_and_deploy_single_codebeamer_project(
                    source=source,
                    target=target,
                    yaml_config=yaml_config,
                    username=username,
                    password=password,
                )
        except yaml.YAMLError as exc:
            logger.exception(exc)


def release_and_deploy_single_codebeamer_project(
        source: str,
        target: str,
        yaml_config: typing.Dict,
        username: str,
        password: str,
        deploy_project: bool = True,
):
    """
    Function reads a release.yaml file and :
        - creates a baseline in the source project
        - exports the source project and uploads the files to codebeamer/document of the source project (until
          artifactory is available)
        - deploys the codebeamer project to the target server (optional)
        - creates a baseline in the deployed target project (optional)
    Raises exception if not successful.
    :param source: URL or shortname of the source server
    :param target: URL or shortname of the target server
    :param yaml_config: Path of the release.yaml file
    :param username: user name for source and target server
    :param password: password for source and target server
    :param deploy_project: if True (default) the project will be deployed on target system and the corresponding
        baselines will be created.
    """

    logger.info(
        f"running selective release and deployment for project {yaml_config['project']}"
    )

    cb_source_server = calmpy.Server(
        url=source, user=username, password=password, readonly=False
    )
    cb_target_server = calmpy.Server(
        url=target, user=username, password=password, readonly=False
    )
    branch_name = os.getenv("Build.SourceBranchName")

    logger.info(yaml_config)

    # on main we only deploy the already exported deployment zips
    if branch_name != "main":
        logger.info("Create release artifacts...")
        create_release_artifacts(cb_server=cb_source_server, yaml_config=yaml_config)

    # only deploy the exported files when requested
    if branch_name == "main" and "exclude_from_productive_deployment" in yaml_config and yaml_config["exclude_from_productive_deployment"] is True:
        deploy_project = False

    if deploy_project:
        logger.info(f"Deploy codebeamer project to {cb_target_server.URL}")
        deploy_codebeamer_project(
            cb_source_server=cb_source_server,
            cb_target_server=cb_target_server,
            yaml_config=yaml_config,
        )

    # when we released to project to the productive instance (TMPL/PROD), we put the files into the source project
    # as projectBase.zip/template.zip to enable the users to use ProjectDiff
    # additionally the exported deployment template will be stored in the source project.
    if branch_name == "main":
        logger.info("Release deployed artifacts to source project...")

        release_deployed_artifacts(
            cb_source_server=cb_source_server,
            yaml_config=yaml_config,
        )


def create_release_artifacts(cb_server: calmpy.Server, yaml_config: typing.Dict):
    """
    Creates a baseline in the given project, exports the project deployment and template zip. Creates baseline before
    and after export.
    :param cb_server: Server instance to operate on
    :param yaml_config: release.yaml content
    """

    with tempfile.TemporaryDirectory() as save_dir:
        project_name = yaml_config["project"]
        project_version = yaml_config["project_version"]
        cb_project = cb_server.get_project(project_name)
        _create_baseline(
            cb_project=cb_project,
            yaml_config=yaml_config,
            baseline_suffix="pre release",
            description="Baseline before release",
        )

        deploy_dependencies = False
        if "deploy_dependencies" in yaml_config:
            deploy_dependencies = yaml_config["deploy_dependencies"]

        # exporting the deployment to a file until in-memory handling works
        file_content_template = cb_project.export_as_template(save_dir=save_dir)
        file_content_deployment = cb_project.export_for_deployment(
            save_dir=save_dir, with_depending_projects=deploy_dependencies
        )

        file_content_template_name = (
            f"{project_version}.{cb_project.template_export_name}"
        )
        file_content_deployment_name = (
            f"{project_version}.{cb_project.deployment_export_name}"
        )

        # upload project template as template.zip / projectBase.zip
        cb_project.upload_documents(
            document=os.path.join(save_dir, cb_project.template_export_name),
            project_path=_get_project_path(yaml_config=yaml_config),
            filename=file_content_template_name,
            description=project_version,
            comment=project_version,
        )

        # not really needed in project, but easier to have in documents
        cb_project.upload_documents(
            document=os.path.join(save_dir, cb_project.deployment_export_name),
            project_path=_get_project_path(yaml_config=yaml_config),
            filename=file_content_deployment_name,
            description=project_version,
            comment=project_version,
        )

        _create_baseline(
            cb_project=cb_project,
            yaml_config=yaml_config,
            baseline_suffix="post release",
            description="Baseline after release",
        )


def deploy_codebeamer_project(
        cb_source_server: calmpy.Server,
        cb_target_server: calmpy.Server,
        yaml_config: typing.Dict,
):
    """
    Deploys the project from the given release.yaml from the source project to the target server
    :param cb_source_server: Server instance of source project
    :param cb_target_server: Target Server instance
    :param yaml_config: release.yaml
    """

    with tempfile.TemporaryDirectory() as save_dir:

        project_name = yaml_config["project"]
        baseline_name = yaml_config["project_version"]
        cb_source_project = cb_source_server.get_project(project_name)
        try:
            cb_target_project = cb_target_server.get_project(project_name)
        except calmpy.ProjectNotFound:
            cb_target_project = None

        if cb_target_project is not None:
            _create_baseline(
                cb_project=cb_target_project,
                yaml_config=yaml_config,
                baseline_suffix="pre deployment",
                description="baseline before deployment",
            )

        template_deployment_zip_path = os.path.join(
            save_dir, f"{baseline_name}.{cb_source_project.deployment_export_name}"
        )

        cb_source_project.download_document(
            document=f"{baseline_name}.{cb_source_project.deployment_export_name}",
            save_dir=save_dir,
        )

        # HD
        cb_target_server.deploy_project_horizontal(
            file_path=template_deployment_zip_path
        )
        cb_target_server.projects = None  # reset calmpy project cache
        cb_target_project = cb_target_server.get_project(project_name)

        _create_baseline(
            cb_project=cb_target_project,
            yaml_config=yaml_config,
            baseline_suffix="post deployment",
            description="baseline after deployment",
        )

        # when deploying a project for the first time, no tracker is marked as template tracker, so we patch all of them
        # FIXME: maybe only leaf tracker available as template
        # FIXME: Do not do that on PROD or maybe doch?
        trackers = cb_target_project.get_trackers()
        tracker_attr = {"availableAsTemplate": True}
        for tracker in trackers:
            tracker.update_tracker_config(tracker_attr)


def release_deployed_artifacts(
        cb_source_server: calmpy.Server,
        yaml_config: typing.Dict,
):
    """
    Puts the deployed artifacts into the source project as base for projectDiff and for information.
    The files are read from the source project until artifactory is available
    :param cb_source_server: Server instance with the source project
    :param yaml_config: release.yaml
    """

    with tempfile.TemporaryDirectory() as save_dir:
        project_name = yaml_config["project"]
        cb_source_project = cb_source_server.get_project(project_name)
        project_version = yaml_config["project_version"]
        template_deployment_zip_path = os.path.join(
            save_dir, f"{project_version}.{cb_source_project.deployment_export_name}"
        )
        template_export_template_zip_path = os.path.join(
            save_dir, f"{project_version}.{cb_source_project.template_export_name}"
        )

        cb_source_project.download_document(
            document=f"{os.path.basename(template_deployment_zip_path)}",
            save_dir=save_dir,
        )
        cb_source_project.download_document(
            document=f"{os.path.basename(template_export_template_zip_path)}",
            save_dir=save_dir,
        )

        cb_source_project.upload_documents(
            document=template_deployment_zip_path,
            project_path="",
            filename=f"template.deployment.zip",
            description=project_version,
            comment=project_version,
        )

        cb_source_project.upload_documents(
            document=template_export_template_zip_path,
            project_path="",
            filename=f"template.zip",
            description=project_version,
            comment=project_version,
        )
        cb_source_project.upload_documents(
            document=template_export_template_zip_path,
            project_path="",
            filename=f"projectBase.zip",
            description=project_version,
            comment=project_version,
        )


def _create_baseline(
        cb_project: calmpy.Project,
        yaml_config: typing.Dict,
        baseline_suffix: str,
        description: str,
):
    commit_id = os.getenv("Build.SourceVersion")
    commit_msg = os.getenv("Build.SourceVersionMessage")
    baseline_name = yaml_config["project_version"]
    description = f"{description}\ncommit_id: {commit_id}\ncommit_msg: {commit_msg}"

    logger.info(f"create baseline {baseline_name} in project {cb_project.name}")

    cb_project.create_baseline(
        name=f"{baseline_name}-{baseline_suffix}",
        description=f"{timestamp} {description}",
    )


def _get_project_path(yaml_config: typing.Dict):
    project_version = yaml_config["project_version"]
    project_name = yaml_config["project"]
    project_path = f"exports/{project_name}/{project_version}"
    return project_path


if __name__ == "__main__":
    main()
    logger.info("DONE")