export AWS_ACCOUNT_ID="123456789012"
export AWS_PROFILE="usf123"
export AWS_DEFAULT_REGION="us-east-1"
export AWS_REGION="us-east-1"
export AWS_OUTPUT="json"
#this value is set via the usfl-custom-cdk-deployment-redeploy.sh ~/this-file.sh execution
export AWS_PROJECT_S3_BUCKET_NAME="default-value"
export AWS_TARGET_TOTAL="999999"
# this is the 'public' S3 bucket where compliant pdfs from the result folder are copied to
# the value is automatically set via the run-first-generate-public-bucket-name.sh script
export AWS_DESTINATION_BUCKET_NAME="default-value"
export AWS_DEPLOYMENT_TYPE="test"
# edit the copy of this file manually to replace these placeholder values with your secret key values, and keep the copy of this file out of this repo (or update the .gitignore file)
export AWS_PDF_SERVICES_CLIENT_ID="123456789"
export PDF_SERVICES_CLIENT_ID="123456789"
export AWS_PDF_SERVICES_CLIENT_SECRET="123456789"
export PDF_SERVICES_CLIENT_SECRET="123456789"
export AWS_Default_Email_Address="nobody@no.where"
aws sts get-caller-identity
env | grep AWS | sort
echo "--------------------------------------------------------------"
env | grep PDF_SERVICES | sort
cd ~/path-to-clone-of-this-fork/ 
