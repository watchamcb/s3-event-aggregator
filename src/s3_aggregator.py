import boto3
import botocore
import os
import sys
from datetime import datetime
from dateutil.parser import parse

sqs = boto3.client('sqs')
dynamodb = boto3.client('dynamodb')
refresh_delay = int(os.environ['REFRESH_DELAY_SECONDS'])

def handle_exception(*logs):
    exctype, value = sys.exc_info()[:2]
    print(*logs, exctype, value)

def update_dynamo(bucket, timestamp):
    try:
        dynamodb.update_item(TableName='S3EventAggregator', 
            Key={ 'BucketName' : { 'S': bucket } },
            ExpressionAttributeNames={ '#t': 'timestamp' },
            ExpressionAttributeValues={
                ':t' : { 'N': str(timestamp + refresh_delay) },
                ':x' : { 'N': str(timestamp) }
            }, 
            UpdateExpression = 'SET #t = :t',
            ConditionExpression = 'attribute_not_exists(#t) OR :x > #t')
        # We will only get here if the conditional update succeeded and we need
        # to send a refresh request
        return True
    except botocore.exceptions.ClientError as e:
        if (e.response['Error']['Code'] == 'ConditionalCheckFailedException'):
            print('Refresh for bucket:', bucket, 
                'within refresh window, skipping. S3 Event timestamp:', 
                timestamp)
        else:
            print('ClientError processing DynamoDB update for bucket:', bucket, 
                'ignoring event, exception:', e)
    except:
        handle_exception('Error processing DynamoDB update for bucket:', bucket, 
            'ignoring event, exception:')
    return False

def send_refresh(bucket, timestamp):
    queue = os.environ['QUEUE_URL']
    try:
        sqs.send_message(
            QueueUrl=queue,
            DelaySeconds=refresh_delay,
            MessageAttributes={
                'bucket-name': {
                    'DataType': 'String',
                    'StringValue': bucket
                },
                'timestamp': {
                    'DataType': 'Number',
                    'StringValue': str(timestamp)
                }
            },
            MessageBody='{}')
    except:
        handle_exception('Error sending refresh request for bucket:', bucket, 
            'ignoring event, exception:')

def handle_s3_event(message):
    timestamp = parse(message['eventTime']).timestamp()
    bucket = message['s3']['bucket']['name']
    if update_dynamo(bucket, timestamp):
        send_refresh(bucket, timestamp)

def is_storage_gateway_event(message):
    if 'userIdentity' not in message:
        return False
    source = message['userIdentity']['principalId']
    return source.find('StorageGateway') >= 0

def invalid_event(event):
    if 'Records' not in event:
        return True;
    return False

def lambda_handler(event, context):
    if invalid_event(event):
        print('Ignoring event with unexpected format:', event)
        return
    for message in event['Records']:
        if is_storage_gateway_event(message):
            print('Ignoring Storage Gateway operation:', message)
            continue
        if 's3' not in message:
            print('Ignoring message missing s3 section:', message)
            continue
        handle_s3_event(message)
