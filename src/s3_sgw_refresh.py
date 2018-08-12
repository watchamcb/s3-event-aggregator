import sys
import boto3
import botocore

sgw = boto3.client('storagegateway')
dynamodb = boto3.client('dynamodb')

def find_share(bucket):
    share_list = sgw.list_file_shares()
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
            print('Error looking up NFS file shares,', 
                    'probably an SMB share with wrong execution environment')


def cache_share(bucket, share):
    dynamodb.update_item(TableName='S3EventAggregator', 
        Key={ 'BucketName' : { 'S': bucket } },
        ExpressionAttributeNames={
            '#S' : 'share'
        },
        ExpressionAttributeValues={
            ':s' : { 'S': share }
        }, 
        UpdateExpression = 'SET #S = :s')
    
def lookup_share(bucket):
    response = dynamodb.get_item(
        TableName='S3EventAggregator',
        Key={ 'BucketName' : { 'S': bucket } },
        ExpressionAttributeNames={ '#s': 'share' },
        ProjectionExpression= '#s'
    )
    if 'share' in response['Item']:
        share = response['Item']['share']['S']
        return share
    share = find_share(bucket)
    cache_share(bucket, share)
    return share

def handle_exception(*logs):
    exctype, value = sys.exc_info()[:2]
    print(*logs, exctype, value)

def lambda_handler(event, context):
    if 'Records' not in event:
        print('Ignoring invalid event, missing Records element:', event)
        return
    for message in event['Records']:
        try:
            if 'messageAttributes' not in message:
                print('Ignoring invalid message, missing messageAttributes element:', message)
                continue
            bucket = message['messageAttributes']['bucket-name']['stringValue']
            share = lookup_share(bucket)
            if len(share) > 0:
                print('Refreshing share:', share)
                sgw.refresh_cache(FileShareARN=share)
            else:
                print('Could not find file share for bucket:', bucket, ', skipping refresh')
        except:
            handle_exception('Error proccessing message:', message, ', ignoring')
        
