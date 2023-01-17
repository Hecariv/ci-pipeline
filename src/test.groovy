
import hudson.plugins.git.GitChangeSetList

@NonCPS
def getChangeFiles(GitChangeSetList build) {
    def changeLogSets = build.changeSets
    for (int i = 0; i < changeLogSets.size(); i++) {
        def entries = changeLogSets[i].items
        for (int j = 0; j < entries.length; j++) {
            def entry = entries[j]
            return new ArrayList(entry.affectedFiles)

        }
    }
}