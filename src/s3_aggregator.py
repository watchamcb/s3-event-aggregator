import boto3
import botocore
import os
import sys
from datetime import datetime
from dateutil.parser import parse
import logging

sqs = boto3.client('sqs')
dynamodb = boto3.client('dynamodb')
log = logging.getLogger('S3EventAggregator')
log.setLevel(os.environ['LOG_LEVEL'])
queue = os.environ['QUEUE_URL']
refresh_delay = int(os.environ['REFRESH_DELAY_SECONDS'])

def update_dynamo(bucket, timestamp):
    log.debug('Performing DynamoDB update for bucket %s and timestamp %d',
        bucket, timestamp)
    try:
        dynamodb.update_item(TableName='S3EventAggregator', 
            Key={ 'BucketName' : { 'S': bucket } },
            ExpressionAttributeNames={ '#t': 'timestamp' },
            ExpressionAttributeValues={
                ':t' : { 'N': str(timestamp + (refresh_delay * 1000)) },
                ':x' : { 'N': str(timestamp) }
            }, 
            UpdateExpression = 'SET #t = :t',
            ConditionExpression = 'attribute_not_exists(#t) OR :x > #t')
        # We will only get here if the conditional update succeeded and we need
        # to send a refresh request
        return True
    except botocore.exceptions.ClientError as e:
        if (e.response['Error']['Code'] == 'ConditionalCheckFailedException'):
            log.info('Refresh for bucket: %s within refresh window, '
                'skipping. S3 Event timestamp: %d', bucket, timestamp)
        else:
            log.error('ClientError processing DynamoDB update for bucket: %s'
                ' ignoring event, exception: %s', bucket, e)
    except:
        exctype, value = sys.exc_info()[:2]
        log.error('Error processing DynamoDB update for bucket: %s ' 
            'ignoring event, exception: %s %s', bucket, exctype, value)
    return False

def send_refresh(bucket, timestamp):
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
        exctype, value = sys.exc_info()[:2]
        log.error('Error sending refresh request for bucket: %s '
                'ignoring, exception: %s %s', bucket, exctype, value)

def handle_s3_event(message):
    timestamp = parse(message['eventTime']).timestamp() * 1000
    bucket = message['s3']['bucket']['name']
    if update_dynamo(bucket, timestamp):
        log.info('Sending refresh request for bucket: %s, timestamp: %d', 
            bucket, timestamp)
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
    log.debug('Event received: %s', event)
    if invalid_event(event):
        log.warn('Ignoring event with unexpected format: %s', event)
        return
    for message in event['Records']:
        if is_storage_gateway_event(message):
            log.info('Ignoring Storage Gateway operation: %s', message)
            continue
        if 's3' not in message:
            log.warn('Ignoring message missing s3 section: %s', message)
            continue
        handle_s3_event(message)

