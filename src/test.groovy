
import hudson.plugins.git.GitChangeSetList

def call() {

    writeFile file: 'test.py', text: libraryResource("test.py")

    setupPython{
        String server = "Test"
     //   List files = getChangeFiles()
       // for (int k = 0; k < files.size(); k++) {
         //   def file = files[k]
            withEnv(
                    [
                            'Build_Repository_Name=cb-templates-tmpl',
                            "Build_SourceBranchName=${env.BRANCH_NAME}"
                    ]
            ){
                sh """
                    python3 cb_apply_horizontal_deployment.py --directory . 
                """
            }

        }
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