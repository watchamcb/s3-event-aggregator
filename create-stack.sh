#!/bin/bash
# Requires aws-cli, sed, and zip. Be sure to edit the BUCKET name before running the script

BUCKET=CHANGE-ME
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
AWS_DEFAULT_REGION="eu-west-1"

# Create the DynamoDB table
aws dynamodb create-table --table-name S3EventAggregator --attribute-definitions AttributeName=BucketName,AttributeType=S \
--key-schema AttributeName=BucketName,KeyType=HASH --provisioned-throughput ReadCapacityUnits=5,WriteCapacityUnits=5
aws sqs create-queue --queue-name S3EventAggregatorActionQueue

# Create the SQS queue for processing the aggregated event
aws sqs create-queue --queue-name S3EventAggregatorActionQueue

# Create DynamoDB writer policy
cp iam/dynamo-writer.json .
sed -i "s/ACCOUNT_ID/$ACCOUNT_ID/g" dynamo-writer.json
sed -i "s/REGION/$AWS_DEFAULT_REGION/g" dynamo-writer.json
aws iam create-policy --policy-name S3EventAggregatorDynamo --policy-document file://dynamo-writer.json
rm dynamo-writer.json

# Create SQS writer policy
cp iam/sqs-writer.json .
sed -i "s/ACCOUNT_ID/$ACCOUNT_ID/g" sqs-writer.json
sed -i "s/REGION/$AWS_DEFAULT_REGION/g" sqs-writer.json
aws iam create-policy --policy-name S3EventAggregatorSqsWriter --policy-document file://sqs-writer.json
rm sqs-writer.json

# Create the Lambda STS trust policy
aws iam create-role --role-name S3EventAggregatorLambdaRole --assume-role-policy-document file://iam/lambda-trust.json

# Attach policies to roles
aws iam attach-role-policy --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole --role-name S3EventAggregatorLambdaRole
aws iam attach-role-policy --policy-arn arn:aws:iam::$ACCOUNT_ID:policy/S3EventAggregatorDynamo --role-name S3EventAggregatorLambdaRole
aws iam attach-role-policy --policy-arn arn:aws:iam::$ACCOUNT_ID:policy/S3EventAggregatorSqsWriter --role-name S3EventAggregatorLambdaRole
# Give IAM a chance to propagate
sleep 10

# Create S3EventAggregator function
cp src/s3_aggregator.py .
zip -m s3_aggregator.zip s3_aggregator.py
aws lambda create-function --function-name S3EventAggregator --runtime python3.6 --role arn:aws:iam::$ACCOUNT_ID:role/S3EventAggregatorLambdaRole --zip-file fileb://s3_aggregator.zip --handler s3_aggregator.lambda_handler --timeout 10 --environment "Variables={QUEUE_URL=https://sqs.$AWS_DEFAULT_REGION.amazonaws.com/$ACCOUNT_ID/S3EventAggregatorActionQueue,REFRESH_DELAY_SECONDS=30,LOG_LEVEL=INFO}"
aws lambda put-function-concurrency --function-name S3EventAggregator --reserved-concurrent-executions 1
rm s3_aggregator.zip

# Link the S3EventAggregator function to the bucket
aws lambda add-permission --function-name S3EventAggregator --statement-id SID_$BUCKET --action lambda:InvokeFunction --principal s3.amazonaws.com --source-account $ACCOUNT_ID --source-arn arn:aws:s3:::$BUCKET
cp s3/event.json .
sed -i "s/ACCOUNT_ID/$ACCOUNT_ID/g" event.json
sed -i "s/REGION/$AWS_DEFAULT_REGION/g" event.json
aws s3api put-bucket-notification-configuration --bucket $BUCKET --notification-configuration file://event.json
rm event.json

# Set up the storage gateway and SQS policies then link them to the lambda role
aws iam create-policy --policy-name StorageGatewayRefreshPolicy --policy-document file://iam/sgw-refresh.json
cp iam/sqs-reader.json .
sed -i "s/ACCOUNT_ID/$ACCOUNT_ID/g" sqs-reader.json
sed -i "s/REGION/$AWS_DEFAULT_REGION/g" sqs-reader.json
aws iam create-policy --policy-name S3EventAggregatorSqsReader --policy-document file://sqs-reader.json
rm sqs-reader.json
aws iam create-role --role-name S3AggregatorActionLambdaRole --assume-role-policy-document file://iam/lambda-trust.json
aws iam attach-role-policy --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole --role-name S3AggregatorActionLambdaRole
aws iam attach-role-policy --policy-arn arn:aws:iam::$ACCOUNT_ID:policy/S3EventAggregatorSqsReader --role-name S3AggregatorActionLambdaRole
aws iam attach-role-policy --policy-arn arn:aws:iam::$ACCOUNT_ID:policy/S3EventAggregatorDynamo --role-name S3AggregatorActionLambdaRole
aws iam attach-role-policy --policy-arn arn:aws:iam::$ACCOUNT_ID:policy/StorageGatewayRefreshPolicy --role-name S3AggregatorActionLambdaRole
# Give IAM a chance to propagate
sleep 10

# Create the S3StorageGatewayRefresh function
mkdir deploy
pip install boto3 botocore -t deploy
cp src/s3_sgw_refresh.py deploy
cd deploy
zip -r function.zip *
aws lambda create-function --function-name S3StorageGatewayRefresh --runtime python3.6 --role arn:aws:iam::$ACCOUNT_ID:role/S3AggregatorActionLambdaRole --zip-file fileb://function.zip --handler s3_sgw_refresh.lambda_handler --timeout 5 --environment "Variables={LOG_LEVEL=INFO}"
aws lambda put-function-concurrency --function-name S3StorageGatewayRefresh --reserved-concurrent-executions 1
cd ..
rm -rf deploy

# Link the function to the SQS queue
aws lambda create-event-source-mapping --function-name S3StorageGatewayRefresh --event-source arn:aws:sqs:$AWS_DEFAULT_REGION:$ACCOUNT_ID:S3EventAggregatorActionQueue --batch-size 1
