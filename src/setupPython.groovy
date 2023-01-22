def call (Closure body){
    try {
        // create ~/.netrc file
        withCredentials([usernamePassword(credentialsId: "E3SWToolchain-TechUser", passwordVariable: 'CI_TECH_PWD', usernameVariable: 'CI_TECH_USER')]) {

            sh """
                echo "machine devstack.vwgroup.com" > ~/.netrc
                echo "login \${CI_TECH_USER}" >> ~/.netrc
                echo "password \${CI_TECH_PWD} " >> ~/.netrc
            """
        }

        sh("""
            python3 -m pip install --index-url "https://devstack.vwgroup.com/artifactory/api/pypi/pypi/simple"  --upgrade pip
            python3 -m pip install --index-url "https://devstack.vwgroup.com/artifactory/api/pypi/pypi/simple"  --upgrade wheel
            python3 -m pip install --index-url "https://devstack.vwgroup.com/artifactory/api/pypi/pypi/simple"  --upgrade setuptools_scm
            python3 -m pip install --index-url "https://devstack.vwgroup.com/artifactory/api/pypi/pypi/simple" -r requirements.txt
        """)

        // Run the passed body with ~/.netrc available.
        body()

    } catch (Exception e) {
        echo "ERROR: python dependencies might not be installed"
        throw e
    } finally {
        // delete the files with credentials
        echo "Removing netrc file"
        sh "rm ~/.netrc"
    }

}