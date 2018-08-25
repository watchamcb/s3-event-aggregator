import sys
import os
import boto3
import botocore
import logging

sgw = boto3.client('storagegateway')
dynamodb = boto3.client('dynamodb')
log = logging.getLogger('S3StorageGatewayRefresh')
log.setLevel(os.environ['LOG_LEVEL'])

# Query the Storage Gateway for a list of file shares and then search for
# the share mapped to a bucket.
def find_share(bucket):
    log.debug('Searching for file share gateway for bucket %s', bucket)
    share_list = sgw.list_file_shares()
    log.debug('list_file_shares: %s', share_list)
    nfs_share_arns = []
    smb_share_arns = []
    for share in share_list['FileShareInfoList']:
        # Current Lambda boto3 execution environment is 1.7.30 which does not
        # support the new attribute type, this code tries to handle this by 
        # using the attribute if available or defaulting to NFS, it WILL break
        # if you have SMB shares and due to the API design there is no way to
        # avoid this without including the right version of boto3/botocore in
        # the deployment :(
        if 'FileShareType' in share:
            share_type = share['FileShareType']
        else:
            share_type = 'NFS'
        if share_type == 'NFS':
            nfs_share_arns.append(share['FileShareARN'])
        elif share_type == 'SMB':
            smb_share_arns.append(share['FileShareARN'])
    try:
        if len(nfs_share_arns) > 0:
            result = sgw.describe_nfs_file_shares(FileShareARNList=nfs_share_arns)
            for nfs in result['NFSFileShareInfoList']:
                if nfs['LocationARN'] == ('arn:aws:s3:::' + bucket):
                    return nfs['FileShareARN']
        if len(smb_share_arns) > 0:
            result = sgw.describe_smb_file_shares(FileShareARNList=smb_share_arns)
            for smb in result['SMBFileShareInfoList']:
                if smb['LocationARN'] == ('arn:aws:s3:::' + bucket): 
                    return smb['FileShareARN']
    except botocore.exceptions.ClientError as e:
        if (e.response['Error']['Code'] == 'InvalidGatewayRequestException'):
            log.error('Error looking up NFS file shares,', 
                    'probably an SMB share with wrong execution environment')
    return ''

# Store the share ARN in the DynamoDB table so we don't need to keep querying
# the SGW API for a list of shares. If a refresh cache request fails on a 
# cached share it will be cleared by the remove_cached_share method
def cache_share(bucket, share):
    log.debug('Caching bucket %s file share ARN: %s', bucket, share)
    dynamodb.update_item(TableName='S3EventAggregator', 
        Key={ 'BucketName' : { 'S': bucket } },
        ExpressionAttributeNames={
            '#S' : 'share'
        },
        ExpressionAttributeValues={
            ':s' : { 'S': share }
        }, 
        UpdateExpression = 'SET #S = :s')
    
# Check the DynamoDB table for a cached share ARN. If it is not found then 
# query the SGW API for the available shares and cache the ARN against the
# bucket name for the next lookup.
def lookup_share(bucket):
    response = dynamodb.get_item(
        TableName='S3EventAggregator',
        Key={ 'BucketName' : { 'S': bucket } },
        ExpressionAttributeNames={ '#s': 'share' },
        ProjectionExpression= '#s'
    )
    if 'share' in response['Item']:
        share = response['Item']['share']['S']
        if len(share) > 0:
            log.debug('Cached share %s found for bucket %s', share, bucket)
            return share
    # Cached share was not found, query the API
    share = find_share(bucket)
    if len(share) > 0:
        log.info('Found share %s for bucket %s', share, bucket)
        cache_share(bucket, share)
    return share

# We need to remove stale cached ARNs if a refresh cache request fails. It is
# possible the original (cached) share has been deleted/recreated, clear the 
# cached entry from the DynamoDB table so the next request can do a new lookup
def remove_cached_share(bucket, share):
    log.info('Removing cached bucket %s file share ARN: %s', bucket, share)
    try:
        dynamodb.update_item(TableName='S3EventAggregator', 
            Key={ 'BucketName' : { 'S': bucket } },
            ExpressionAttributeNames={ '#s': 'share' },
            UpdateExpression = 'REMOVE #s')
    except:
        exctype, value = sys.exc_info()[:2]
        log.error('Error clearing cached bucket share %s %s, ignoring: %s', 
                exctype, value, share)

def refresh_sgw_cache(bucket, share):
    log.info('Refreshing share %s for bucket %s', share, bucket)
    try:
        sgw.refresh_cache(FileShareARN=share)
    except:
        exctype, value = sys.exc_info()[:2]
        log.error('Error refreshing cache %s %s, ignoring: %s', exctype, value,
                share)
        # Clear any cached bucket/share 
        remove_cached_share(bucket, share)

# Main handler/entry point. We are expecting an SQS message with attributes
# 'bucket-name' and 'timestamp' set. The bucket name is used to look up the
# Storage Gateway file share ARN to issue a refresh cache command
def lambda_handler(event, context):
    if 'Records' not in event:
        log.warn('Ignoring invalid event, missing Records element: %s', event)
        return
    for message in event['Records']:
        try:
            if 'messageAttributes' not in message:
                log.warn('Ignoring invalid message, missing messageAttributes'
                    ' element: %s', message)
                continue
            bucket = message['messageAttributes']['bucket-name']['stringValue']
            share = lookup_share(bucket)
            if len(share) > 0:
                refresh_sgw_cache(bucket, share)
            else:
                log.warn('Could not find file share, skipping refresh for '
                    'bucket: %s', bucket)
        except:
            exctype, value = sys.exc_info()[:2]
            log.error('Error processing message %s %s, ignoring: %s', exctype, 
                    value, message)

