import argparse
import logging
import os
import datetime
import re
import sys

import time
from contextlib import contextmanager

import yaml

import calmpy
import git
import json
from zipfile import ZipFile

import create_update_codebeamer_project_from_config
import release_and_deploy_codebeamer_project

# use dry_run as global variable, since it needs to be used in almost every method below
dry_run = None

# enable calmpy logging
calmpy.enable_logging(log_level=logging.INFO)
logger = logging.getLogger(__name__)
logger.setLevel("DEBUG")
logger.handlers = logging.getLogger("calmpy").handlers

cb_server = {
    "Prod": "https://codebeamer-poc-prod.azurewebsites.net",
    "Test": "https://codebeamer-poc-test.azurewebsites.net",
    "Dev": "https://codebeamer-poc-dev.azurewebsites.net",
    "Tmpl": "https://codebeamer-poc-template-tmpl.azurewebsites.net",
    "Tmpl-TC": "https://codebeamer-poc-template-tc.azurewebsites.net",
}


def get_args():
    # TODO: check how to use parameters with - in its name
    parser = argparse.ArgumentParser(description="", epilog="")

    parser.add_argument(
        "-p",
        "--post_only",
        action="store_true",
        default=False,
        help="Only run post-processing steps and skip deployment",
    )

    parser.add_argument(
        "-t",
        "--target",
        action="store",
        default="Test",
        help="Target instance for deployment, " "default is Test",
    )

    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        default=False,
        help="Force to deploy ZIPs containing " "multiple projects",
    )

    parser.add_argument(
        "-n", "--dry_run", action="store_true", default=False, help="Dry run"
    )

    parser.add_argument(
        "-c",
        "--check_only_tests",
        action="store_true",
        default=False,
        help="run only the tests",
    )

    parser.add_argument(
        "-dir",
        "--directory",
        action="store",
        default=False,
        help="working dir",
    )

    parser.add_argument(
        "-s",
        "--source",
        action="store",
        default=False,
        help="Codebeamer source server",
    )

    arguments = parser.parse_args()
    return arguments


def get_projects_by_name(deployment_jsons, cb_target):
    cb_projects = cb_target.get_projects()
    for project in cb_projects:
        if project.name in deployment_jsons:
            logger.debug(
                f"associating target instance project id {project.id} with {project.name}"
            )
            deployment_jsons[project.name]["id"] = project.id


def deployment_pre_processing(cb_target, deployment_jsons):
    for project_name in deployment_jsons:
        project = deployment_jsons[project_name]

        if project["id"] > 0:
            logger.info(
                f"Create project baseline prior to deployment in {project_name} ({project['id']})"
            )
            baseline_prefix_name = "Before deployment"
            baseline_data = fetch_baseline_info(baseline_prefix_name, project["id"])
            cb_project = cb_target.get_project(project["id"])
            cb_project.create_baseline(
                name=baseline_data["name"], description=baseline_data["description"]
            )
        else:
            logger.warning(
                f"Project {project['name']} may be new, renamed, or not accessible"
            )


def deployment_post_processing(cb_target, deployment_jsons):
    for project_name in deployment_jsons:
        project = deployment_jsons[project_name]

        # check if deployment was successful
        if project["deployment-result"] is None:
            logger.error(f"Deployment of project {project['name']} failed")
            continue

        # check if project was found
        if project["id"] == 0:
            logger.error(f"Project {project['name']} could not be created")
            continue

        # TODO: copy "changelog" wiki page from TMPL to target?

        # add Project Admin users/groups
        if project["original-id"] == 0:
            logger.warning(
                f"Project {project['name']} was new or got renamed: Check Project Admins manually!"
            )
        add_users_to_project_admin_role(cb_target=cb_target, project_id=project["id"])

        # create baseline as last postprocessing step
        logger.info(
            f"Create project baseline after deployment in {project_name} ({project['id']})"
        )
        baseline_prefix_name = "After deployment"
        baseline_data = fetch_baseline_info(baseline_prefix_name, project["id"])
        cb_project = cb_target.get_project(project["id"])
        cb_project.create_baseline(
            name=baseline_data["name"], description=baseline_data["description"]
        )

        logger.info(f"{cb_target.URL}/project/{project['id']}/members")


def get_main_branch():
    repo = git.Repo(search_parent_directories=True)
    remote_refs = repo.remote().refs

    for refs in remote_refs:
        logger.info(refs.name)
    heads = repo.heads
    try:
        main_branch = heads.main
    except AttributeError:
        logger.info("no local main branch available")
        main_branch = "origin/main"
    return main_branch


def determine_changeset_by_commit(path_to_repo: str):
    repo = git.Repo(search_parent_directories=True)
    current_commit = repo.commit("HEAD")  # FIXME: check how we can always use .head
    previous_commit = repo.merge_base(get_main_branch(), repo.head)[0]
    # we are not running on a branch
    if previous_commit == current_commit:
        previous_commit = repo.commit("HEAD~1")
    logger.info(
        f"current commit: {current_commit.hexsha}, previous commit: {previous_commit}"
    )
    diff_commits = previous_commit.diff(current_commit)
    return diff_commits


def modified_or_added(diff):
    return diff.change_type == "M" or diff.change_type == "A"


def store_project_info(
        deployment_zips, deployment_jsons, commit_file, zip_file, zipped_file
):
    project_json = json.loads(zip_file.read(zipped_file.filename))
    project_name = project_json["project"]["name"]
    project_info = {
        "name": project_name,
        "id": 0,
        "original-id": 0,
        "deployment-result": None,
        "project-json": project_json,
        "zip": commit_file,
        "project-file": zipped_file.filename,
    }
    deployment_jsons[project_name] = project_info
    deployment_zips[commit_file].append({project_name: project_info})


def store_original_project_id(deployment_jsons):
    # copy current project id to original-id for post-processing
    for project_name in deployment_jsons:
        project = deployment_jsons[project_name]
        project["original-id"] = project["id"]


def store_deployment_result(deployment_jsons, zip_filename, result):
    # result may be True (success) or None (failure), currently
    for project_name in deployment_jsons:
        if deployment_jsons[project_name]["zip"] == zip_filename:
            deployment_jsons[project_name]["deployment-result"] = result


def determine_source_stage():
    logger.info("determine source stage")
    repo_name = os.getenv("Build_Repository_Name")
    logger.info(f"running in repo {repo_name}")
    if repo_name == "cb-templates-tc":
        source = cb_server["Tmpl-TC"]
    else:
        source = cb_server["Tmpl"]
    logger.info(f"CB Source Server: {source}")
    return source


def determine_target_stage():
    logger.info(f"Envs = {os.environ}")
    repo_name = os.getenv("Build_Repository_Name")
    logger.info(f"running in repo {repo_name}")
    branch_name = os.getenv(
        "Build_SourceBranchName"
    )  # branch name from the repo which triggers the build
    logger.info(f"running on branch {branch_name}")
    target = ""

    if repo_name == "cb-templates-tmpl":
        if branch_name != "main":
            target = cb_server["Test"]
        else:
            target = cb_server["Prod"]

    if repo_name == "cb-templates-tc":
        if branch_name != "main":
            target = cb_server["Dev"]
        else:
            target = cb_server["Tmpl"]

    logger.info(f"CB Target Server: {target}")
    return target


def fetch_commit_info():
    repo = git.Repo(search_parent_directories=True)
    current_commit = repo.head.commit
    author = current_commit.author
    author_mail = current_commit.author.email
    sha = current_commit.hexsha
    short_sha = repo.git.rev_parse(sha, short=6)
    msg = current_commit.message

    data = {
        "commit_author": author,
        "commit_author_mail": author_mail,
        "commit_message": msg,
        "commit_short_id": short_sha,
        "commit_sha1": sha,
    }
    return data


def fetch_baseline_info(baseline_prefix_name, project_id):
    data = fetch_commit_info()

    date = datetime.datetime.now().strftime("%Y_%m_%dT%H_%M_%S")
    baseline_data = {
        "name": f"{baseline_prefix_name} - {data['commit_sha1']} ({date})",
        "description": f"{data['commit_author']}: {data['commit_message']}",
        "project": {"id": project_id},
    }
    return baseline_data


def countdown(t, output_every_x_seconds=5):
    while t:
        mins, secs = divmod(t, 60)
        timer = "{:02d}:{:02d}".format(mins, secs)
        if t % output_every_x_seconds == 0:
            logger.debug(f"{timer}\r")
        time.sleep(1)
        t -= 1

    logger.info("Finished")


def all_files_in_dir():
    from pathlib import Path

    # All files and directories ending with .txt and that don't begin with a dot:
    path = os.getcwd()

    logger.debug(f"all zip files in {path}")
    for path in Path(path).rglob("*.zip"):
        print(path)


def get_project_admin_uri():
    return "role/1"


def get_alm_deploy_group_uri():
    return "group/ALM-DEPLOY"


def get_user_uri(user: str):
    return "user/" + user


def add_users_to_project_admin_role(cb_target, project_id):
    logger.info("Adding users to Project Admin role")
    # whom do we always want to add as project admins (for now at least)
    users = ["fixcbjf", "fixc9ms"]

    commit_author = get_cb_username_from_commit_author(cb_target=cb_target)
    users.append(commit_author)
    project_admin_uri = get_project_admin_uri()
    for user in users:
        user_uri = get_user_uri(user)
        url = (
            f"{cb_target.URL}/rest/project/{project_id}/{project_admin_uri}/{user_uri}"
        )
        response = cb_target.session.make_single_request(
            request_url=url, request_type="PUT"
        )
        if response is None:
            logger.debug(f"Add user to project {project_id}: {user}")

    # add ALM-Deploy group
    alm_deploy_group_uri = get_alm_deploy_group_uri()
    url = f"{cb_target.URL}/rest/project/{project_id}/{project_admin_uri}/{alm_deploy_group_uri}"
    response = cb_target.session.make_single_request(
        request_url=url, request_type="PUT"
    )
    if response is None:
        logger.debug(f"Add group to project {project_id}: {alm_deploy_group_uri}")


def extract_names_from_mail(mail: str):
    pattern = r"(extern.)?([A-Za-z0-9]*)\.?([A-Za-z0-9]*).*@"
    matches = re.match(pattern, mail)
    data = {}
    if matches:
        matching_groups = [
            match for match in matches.groups() if match and match != "extern."
        ]
        # [extern.]user_id@mail.de
        if len(matching_groups) == 1:
            data["name"] = matching_groups[0]
            logger.debug(f"Found unique user name: {matching_groups[0]}")
        # [extern.]firstName.lastName@mail.de
        elif len(matching_groups) == 2:
            # this is needed if the name has a number at the end (multiple users with the same name)
            first_name = "".join([i for i in matching_groups[0] if not i.isdigit()])
            last_name = "".join([i for i in matching_groups[1] if not i.isdigit()])

            data["firstName"] = first_name
            data["lastName"] = last_name

            logger.debug(f"Found user first name: {first_name}")
            logger.debug(f"Found user first name: {last_name}")
        else:
            logger.warning("Found more than one/two names. Please check the email.")
    return data


def extract_names_from_git(commit_author):
    # last and first name
    first_name = None
    last_name = None
    # account name
    name = None

    logger.debug(f"git commit author: {commit_author.name}")
    if "," in commit_author.name:
        # format seems to be "lastname, firstname (extra-characters)", manually tested with:
        # Last Name, Firstname (Orga)
        # Last Name, Firstname
        # Lastname, Firstname (Orga)
        # Lastname , Firstname  (Orga)
        # Lastname, Firstname
        # Lastname, Firstname, Dr. (Orga)
        # Last-Name, Some First Name (Orga)
        author = str(commit_author.name).split(",")
        first_name = author[1]
        last_name = author[0]
        if "(" in first_name:
            remove_orga = str(first_name).split("(")
            first_name = remove_orga[0]
    elif " " in commit_author.name:
        # format seems to be "firstname lastname", e.g. "Daniel Bachran"
        author = str(commit_author.name).split()
        first_name = author[0]
        last_name = author[1]
    elif "\\" in commit_author.name:
        # format seems to be "domain\account", e.g. "DEVWAG00\FIXC9MS", remove the domain
        author = re.sub(".*\\\\", "", commit_author.name)
        name = str(author)
    elif len(commit_author.name) == 7:
        # assume that we have an account here, e.g. "FIXC9MS"
        # (NOTE: all accounts have 7 chars and have to be lower case)
        name = str(commit_author.name).lower()

    data = {}
    if last_name and first_name:
        first_name = first_name.strip()
        last_name = last_name.strip()
        logger.debug(f"searching for firstName {first_name} and lastName {last_name}")
        data["firstName"] = first_name
        data["lastName"] = last_name
    if name:
        name = name.strip()
        logger.debug(f"searching for name {name}")
        data["name"] = name
    return data


def get_cb_username_from_commit_author(cb_target):
    commit_data = fetch_commit_info()
    commit_author = commit_data["commit_author"]

    # email address
    email = commit_data["commit_author_mail"]

    # ________________________________________________________________________________
    # 1. search for email address only
    data = {}
    logger.debug(f"searching for email only: {email}")
    data["email"] = email
    response = get_user_from_codebeamer(cb_target, data)

    if response["total"] == 0:
        # ________________________________________________________________________________
        # 2. attempt to determine first and last name and search for those only
        data = extract_names_from_git(commit_author)
        response = get_user_from_codebeamer(cb_target, data)

        if response["total"] > 1:
            raise ValueError(
                f"Commit author {data} not unique in codebeamer ({response['total']} found)"
            )

        if response["total"] == 0:
            # ________________________________________________________________________________
            # 3. try to determine first and last name from email address
            logger.debug("try getting unique user name from email adress...")
            data = extract_names_from_mail(email)
            response = get_user_from_codebeamer(cb_target, data)

            if response["total"] != 1:
                raise ValueError(
                    f"Commit author {data} not unique/available in codebeamer ({response['total']} found)"
                )

    username_commit_author = response["users"][0]["name"]
    logger.debug(f"got user {username_commit_author}")
    return username_commit_author


def get_user_from_codebeamer(cb_target, data):
    if dry_run:  # even if dry-running only, we want to fetch user info from codebeamer
        cb_target.readonly = False

    response = cb_target.session.make_single_request(
        request_url=f"{cb_target.URL}/api/v3/users/search?page=1&pageSize=1",
        request_type="POST",
        data=data,
    )
    logger.debug(f"number of results: {response['total']}")

    if dry_run:  # need to restore the previous state
        cb_target.readonly = True
    return response


def assert_zip_present(nr, deployment_zips, force):
    logger.info(f"Test {nr}: Ensure that a deployment ZIP is found")

    logger.debug(f"Number of deployment ZIPs: {len(deployment_zips)}")
    test_result = "Passed"
    if len(deployment_zips) == 0:
        message = "NO deployment ZIP found"
        if force:
            logger.warning(message)
            test_result = "Passed (forced)"
        else:
            logger.error("Deployment aborted: " + message)
            raise Exception(message)

    logger.info(f"Test {nr}: {test_result}")


def assert_zip_contains_only_one_project(nr, deployment_zips, force):
    logger.info(f"Test {nr}: Ensure that deployment ZIP contains only one project")

    test_result = "Passed"
    offending_zips = []
    for filename in deployment_zips:
        logger.debug(filename)
        project_list = deployment_zips[filename]
        if len(project_list) > 1:
            offending_zips.append(filename)

    if len(offending_zips) > 0:
        message = f"More than one project in: {offending_zips}"
        if force:
            logger.warning(message)
            test_result = "Passed (forced)"
        else:
            logger.error("Deployment aborted: " + message)
            raise Exception(message)

    logger.info(f"Test {nr}: {test_result}")


def assert_zip_contains_deployment_files(nr, deployment_zips):
    logger.info(
        f"Test {nr}: Ensure that deployment ZIP contains deployment files (i.e. is a deployment ZIP)"
    )

    test_result = "Passed"
    for filename in deployment_zips:
        logger.debug(filename)
        # Search deployment ZIP for multiExport.txt, which must be present (otherwise it is no deployment ZIP)
        with ZipFile(filename, "r") as zip_file:
            for zipped_file in zip_file.infolist():
                if zipped_file.filename == "multiExport.txt":
                    break
            else:
                message = f"Invalid ZIP contents for a deployment ZIP (missing 'multiExport.txt'): {filename}"
                logger.error("Deployment aborted: " + message)
                raise Exception(message)

    logger.info(f"Test {nr}: {test_result}")


def assert_filename_consistency(nr, deployment_zips, force):
    logger.info(f"Test {nr}: Check folder name/zip name consistency")

    test_result = "Passed"
    for filepath in deployment_zips:
        logger.debug(filepath)

        # fetch filename
        parts_file = os.path.split(filepath)
        filename = parts_file[1]
        basename = filename.rpartition(".deployment.zip")[0]

        folder_name = os.path.basename(parts_file[0])

        logger.debug(f"Base ZIP filename: {basename}, folder name: {folder_name}")

        if basename != folder_name:
            message = f"Deployment ZIP filename ({basename}) does not match parent folder name: {folder_name}"
            if force:
                logger.warning(message)
                test_result = "Passed (forced)"
            else:
                logger.error("Deployment aborted: " + message)
                raise Exception(message)

    logger.info(f"Test {nr}: {test_result}")


def assert_namespace_present(nr, deployment_jsons, force):
    logger.info(f"Test {nr}: Check project name/key for namespaces")

    test_result = "Passed"
    for project_name in deployment_jsons:
        project = deployment_jsons[project_name]

        json_project = get_json_project(project)
        json_project_namespace = get_json_project_namespace(json_project)
        json_key = get_json_key(project)
        json_key_namespace = get_json_key_namespace(json_key)
        logger.debug(f"project: {json_project}, key: {json_key}")

        if len(json_project_namespace) == 0 or len(json_key_namespace) == 0:
            message = f"Namespace not present for project ({json_project}) or project key ({json_key})"
            if force:
                logger.warning(message)
                test_result = "Passed (forced)"
            else:
                logger.error("Deployment aborted: " + message)
                raise Exception(message)

    logger.info(f"Test {nr}: {test_result}")


def assert_namespace_consistency(nr, deployment_jsons, force):
    logger.info(f"Test {nr}: Check project name/key namespaces for consistency")

    test_result = "Passed"
    for project_name in deployment_jsons:
        project = deployment_jsons[project_name]

        json_project = get_json_project(project)
        json_project_namespace = get_json_project_namespace(json_project)
        json_key = get_json_key(project)
        json_key_namespace = get_json_key_namespace(json_key)
        logger.debug(f"project: {json_project}, key: {json_key}")

        if json_project_namespace != json_key_namespace:
            message = f"Namespaces differ between project ({json_project}) and project key ({json_key})"
            if force:
                logger.warning(message)
                test_result = "Passed (forced)"
            else:
                logger.error("Deployment aborted: " + message)
                raise Exception(message)

    logger.info(f"Test {nr}: {test_result}")


def assert_valid_commit_author(nr, cb_target, force):
    logger.info(f"Test {nr}: Commit author can be found in codebeamer")

    test_result = "Passed"
    try:
        get_cb_username_from_commit_author(cb_target=cb_target)
    except ValueError as e:
        message = str(e)
        if force:
            logger.warning(message)
            test_result = "Passed (forced)"
        else:
            logger.error("Deployment aborted: " + message)
            raise Exception(message)

    logger.info(f"Test {nr}: {test_result}")


def assert_zip_contains_correct_project(nr, deployment_jsons, force):
    logger.info(f"Test {nr}: Ensure that deployment ZIP contains correct project JSON")

    test_result = "Passed"
    for project_name in deployment_jsons:
        project = deployment_jsons[project_name]

        filepath = project["zip"]
        parts_file = os.path.split(filepath)
        filename = parts_file[1]
        zip_name = filename.rpartition(".deployment.zip")[0]

        project_file = project["project-file"]
        json_name = project_file.rpartition(".json")[0]
        logger.debug(f"ZIP basename: {zip_name}, JSON basename: {json_name}")

        if zip_name != json_name:
            message = f"Deployment ZIP filename ({zip_name}) does not match project JSON filename: {json_name}"
            if force:
                logger.warning(message)
                test_result = "Passed (forced)"
            else:
                logger.error("Deployment aborted: " + message)
                raise Exception(message)

    logger.info(f"Test {nr}: {test_result}")


def get_json_key_namespace(json_key):
    return re.sub("-.+", "", json_key)


def get_json_key(project):
    return project["project-json"]["project"]["keyName"]


def get_json_project_namespace(json_project):
    return re.sub("-.+", "", json_project)


def get_json_project(project):
    return project["project-json"]["project"]["name"]


def assert_cross_references_in_deployment_json(nr, deployment_jsons, force):
    logger.info(f"Test {nr}: Assert that cross-references are in the deployment JSON")

    test_result = "Passed"
    for project_name in deployment_jsons:
        project = deployment_jsons[project_name]
        logger.debug(project_name)

        if "crossReferences" not in project["project-json"]["project"]:
            message = f"Deployment JSON does not contain key 'crossReferences' (incomplete file?)"
            if force:
                logger.warning(message)
                test_result = "Passed (forced)"
            else:
                logger.error("Deployment aborted: " + message)
                raise Exception(message)

    logger.info(f"Test {nr}: {test_result}")


def check_no_external_project_references(nr, deployment_jsons):
    logger.info(f"Test {nr}: Check for external project references in JSON")

    external_references = {}
    for project_name in deployment_jsons:
        project = deployment_jsons[project_name]
        logger.debug(project_name)

        for cross_reference in project["project-json"]["project"]["crossReferences"]:
            if "projectId" in cross_reference:
                external_references[project_name] = {
                    "external-id": cross_reference["projectId"],
                    "external-name": cross_reference["projectName"],
                    "path": cross_reference["path"],
                }
                logger.debug(
                    f"{cross_reference['projectName']} ({cross_reference['projectId']}): {cross_reference['path']}"
                )

    # we must not abort deployment, since some external references may be desired
    if len(external_references.keys()) > 0:
        message = (
            f"External project references found in: {list(external_references.keys())}"
        )
        logger.warning(message)
        logger.info(f"Test {nr}: Inconclusive, deployment may fail")
    else:
        logger.info(f"Test {nr}: Passed")


@contextmanager
def cwd(path):
    oldpwd = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(oldpwd)


def is_mixed_commit(diffs: git.DiffIndex) -> bool:
    content_detected = False
    release_detected = False
    for diff in diffs:
        filepath = diff.b_path
        if "content." in filepath.lower():
            content_detected = True
        elif "release." in filepath.lower():
            release_detected = True

    if release_detected and content_detected:
        return True

    return False


def main():
    args = get_args()
    target = determine_target_stage()
    source = determine_source_stage()

    # need to make dry_run global, since we have to check this also in sub-functions as well
    global dry_run
    dry_run = args.dry_run
    post_processing_only = args.post_only
    check_only_tests = args.check_only_tests

    # directory to run against (needed for git history check) => if not given run cwd
    if "directory" in args:
        os.chdir(args.directory)

    deployment_zips = {}
    deployment_jsons = {}

    diff_commits = determine_changeset_by_commit(path_to_repo=args.directory)

    files_in_diff = {}
    for diff in diff_commits:
        commit_file = diff.b_path
        logger.info(f"{diff.change_type}:{commit_file}")

        # only consider modified/added files
        if not modified_or_added(diff):
            continue

        if commit_file.lower().endswith(".yaml") or commit_file.lower().endswith(
                ".yml"
        ):
            files_in_diff[commit_file] = True

    # check for mixed pipelines with release and content
    mixed_pipeline = is_mixed_commit(diff_commits)
    if mixed_pipeline:
        msg = "mixed pipeline detected: content creation and release\nplease recommit on different branches"
        logger.error(f"{msg}")
        raise ValueError(f"{msg}")

    failure_in_pipeline = False
    for config_file in files_in_diff:

        logger.info(f"{config_file}")

        yaml_config = yaml.safe_load(open(config_file, "r"))

        try:
            logger.debug(f"YAML: {config_file}")
            if os.path.basename(config_file).lower().startswith("release."):
                release_and_deploy_codebeamer_project.release_and_deploy_codebeamer_project(
                    source=source, target=target,
                    yaml_path=config_file,
                )
            elif os.path.basename(config_file).lower().startswith("content."):
                create_update_codebeamer_project_from_config.create_update_codebeamer_project_from_config(
                    server=source, config_file=config_file
                )
            else:
                logger.info(f"ignore yaml file {config_file}")
        except Exception as e:
            failure_in_pipeline = True
            logger.error(f"error during running scripts for: {config_file}")
            logger.exception(e)

    if failure_in_pipeline:
        logger.error("at least one pipeline failed")
        sys.exit(1)


if __name__ == "__main__":
    main()