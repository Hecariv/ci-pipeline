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



get_main_branch()