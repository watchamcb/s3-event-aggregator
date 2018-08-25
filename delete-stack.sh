#!/bin/bash
# Requires aws-cli. Be sure to edit the BUCKET name before running the script

BUCKET=CHANGE-ME
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
AWS_DEFAULT_REGION="eu-west-1"

# Remove SQS queue to Lambda link and Lambda function
UUID=$(aws lambda list-event-source-mappings --function-name S3StorageGatewayRefresh --query 'EventSourceMappings[0].UUID' --output text); aws lambda delete-event-source-mapping --uuid $UUID
aws lambda delete-function --function-name S3StorageGatewayRefresh

# Remove Lambda role, storage gateway and SQS policies
aws iam detach-role-policy --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole --role-name S3AggregatorActionLambdaRole
aws iam detach-role-policy --policy-arn arn:aws:iam::$ACCOUNT_ID:policy/S3EventAggregatorSqsReader --role-name S3AggregatorActionLambdaRole
aws iam detach-role-policy --policy-arn arn:aws:iam::$ACCOUNT_ID:policy/S3EventAggregatorDynamo --role-name S3AggregatorActionLambdaRole
aws iam detach-role-policy --policy-arn arn:aws:iam::$ACCOUNT_ID:policy/StorageGatewayRefreshPolicy --role-name S3AggregatorActionLambdaRole
aws iam delete-policy --policy-arn arn:aws:iam::$ACCOUNT_ID:policy/StorageGatewayRefreshPolicy
aws iam delete-policy --policy-arn arn:aws:iam::$ACCOUNT_ID:policy/S3EventAggregatorSqsReader
aws iam delete-role --role-name S3AggregatorActionLambdaRole

# Remove bucket permissions
aws lambda remove-permission --function-name S3EventAggregator --statement-id SID_$BUCKET

# This will remove all S3 event notifications from the bucket, only uncomment if there are no other events configured
# Use the AWS Console to only delete the s3-event-aggregator notification if you are not okay with wiping out all
# of the event notifications on this bucket
#aws s3api put-bucket-notification-configuration --bucket $BUCKET --notification-configuration {}

# Remove Lambda function
aws lambda delete-function --function-name S3EventAggregator

# Detach policies and roles
aws iam detach-role-policy --policy-arn arn:aws:iam::$ACCOUNT_ID:policy/S3EventAggregatorDynamo --role-name S3EventAggregatorLambdaRole
aws iam detach-role-policy --policy-arn arn:aws:iam::$ACCOUNT_ID:policy/S3EventAggregatorSqsWriter --role-name S3EventAggregatorLambdaRole
aws iam detach-role-policy --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole --role-name S3EventAggregatorLambdaRole
aws iam delete-role --role-name S3EventAggregatorLambdaRole 
aws iam delete-policy --policy-arn arn:aws:iam::$ACCOUNT_ID:policy/S3EventAggregatorSqsWriter
aws iam delete-policy --policy-arn arn:aws:iam::$ACCOUNT_ID:policy/S3EventAggregatorDynamo

# Delete the SQS queue
aws sqs delete-queue --queue-url https://$AWS_DEFAULT_REGION.queue.amazonaws.com/$ACCOUNT_ID/S3EventAggregatorActionQueue

# Delete the DynamoDB table
aws dynamodb delete-table --table-name S3EventAggregator 

