import com.cloudbees.groovy.cps.NonCPS


def call() {

    writeFile file: 'requirements.txt', text: libraryResource("requirements.txt")
    writeFile file: 'create_update_codebeamer_project_from_config.py', text: libraryResource("create_update_codebeamer_project_from_config.py")
    writeFile file: 'cb_apply_horizontal_deployment.py', text: libraryResource("cb_apply_horizontal_deployment.py")
    writeFile file: 'release_and_deploy_codebeamer_project.py', text: libraryResource("release_and_deploy_codebeamer_project.py")
    writeFile file: 'resources/CARIAD_Empty_Template.zip', text: libraryResource("resources/CARIAD_Empty_Template.zip")

    setupPython{
        String server = "Test"

        withEnv(
                [
                        'Build_Repository_Name=cb-templates-tmpl',
                        "Build_SourceBranchName=${env.BRANCH_NAME}"
                ]
        ){
            sh """
                    echo 'TEST'
                    python3 cb_apply_horizontal_deployment.py --directory . 
                """
        }
// Currently not needed, since determination of changeset is already part of the python script. We have to decide which approach we'll use in the future
//        List files = getChangeFiles()
//        for (int k = 0; k < files.size(); k++) {
//            def file = files[k]
//            withEnv(
//                    [
//                            'Build_Repository_Name=cb-templates-tmpl',
//                            "Build_SourceBranchName=${env.BRANCH_NAME}"
//                    ]
//            ){
//                sh """
//                    python3 cb_apply_horizontal_deployment.py --directory .
//                """
//            }
//
//        }
    }

}

@NonCPS
def getChangeFiles() {
    def fileList = []
    def changeLogSets = currentBuild.changeSets
    for (int i = 0; i < changeLogSets.size(); i++) {
        def entries = changeLogSets[i].items
        for (int j = 0; j < entries.length; j++) {
            def entry = entries[j]
            def files = new ArrayList(entry.affectedFiles)
            for (int k = 0; k < files.size(); k++) {
                def file = files[k]
                fileList.add(file.path)
            }


        }
    }
    return fileList
}