import git
import logging
import os
import datetime
import re
import sys


logger = logging.getLogger(__name__)
logger.setLevel("DEBUG")

def get_main_branch():
    repo = git.Repo(search_parent_directories=True)
    remote_refs = repo.remote().refs
    logger.info("HELLOOOOOOOOOOOOOOOOOOOOOO")
    for refs in remote_refs:
        logger.info(refs.name)
    heads = repo.heads
    try:
        main_branch = heads.main
    except AttributeError:
        logger.info("no local main branch available")
        main_branch = "origin/main"
    return main_branch


def main():
    get_main_branch()


if __name__ == "__main__":
    main()