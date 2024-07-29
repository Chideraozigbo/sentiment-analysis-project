"""
Sentiment Analysis Pipeline

This script performs an ETL (Extract, Transform, Load) process on Twitter data. It includes:
- Extraction of tweets from the Twitter API
- Transformation of raw tweet data into structured CSV files
- Uploading of raw and processed data to an AWS S3 bucket

The script also implements deduplication of tweets across multiple runs by maintaining
a record of processed tweet IDs.
"""
#%%
# Import Libraries
import configparser
import requests
import pandas as pd
import json
from datetime import datetime
import re
import emoji
from textblob import Word
from nltk.corpus import stopwords
from nltk.tokenize import word_tokenize
import nltk
import boto3
import os
from botocore.exceptions import ClientError
import time
from requests.exceptions import RequestException

# Download the stopwords dataset and the punkt tokenizer models from NLTK
nltk.download('stopwords', quiet=True)
nltk.download('punkt', quiet=True)

# Load my secret file
config = configparser.ConfigParser()
config.read('/Users/user/Documents/Sentiment Analysis Pipeline/config/secrets.ini')

# Retries Constant
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds

# Access API KEY
api_key = config['API_KEY']['x-rapidapi-key']
api_host = config['API_KEY']['x-rapidapi-host']

# AWS ACCESS KEY
ACCESS_KEY_ID = config['AWS_CREDENTIALS']['ACCESS_KEY_ID']
SECRET_ACCESS_KEY = config['AWS_CREDENTIALS']['SECRET_ACCESS_KEY']

# S3 bucket name
BUCKET_NAME = 'sentimentanalysisprojectpipeline' 

# Time format 
time_format = '%Y-%m-%d %H:%M:%S'
now = datetime.now()
formatted_time = now.strftime(time_format)

# Parameters
raw_file_path = '/Users/user/Documents/Sentiment Analysis Pipeline/data/raw/'
raw_file_name = f'raw_data_{formatted_time}.json'
raw_file_format = os.path.join(raw_file_path, raw_file_name)
log_file_path = '/Users/user/Documents/Sentiment Analysis Pipeline/logs/'
log_file = os.path.join(log_file_path, 'etl_log.txt')
processed_tweet_ids_file = os.path.join(log_file_path, 'processed_tweet_hashes.txt')

# CSV file paths
users_csv_path = f'/Users/user/Documents/Sentiment Analysis Pipeline/data/processed/users/users_{formatted_time}.csv'
tweets_csv_path = f'/Users/user/Documents/Sentiment Analysis Pipeline/data/processed/users_tweet/tweets_{formatted_time}.csv'

# Create necessary directories
os.makedirs(raw_file_path, exist_ok=True)
os.makedirs(log_file_path, exist_ok=True)
os.makedirs(os.path.dirname(users_csv_path), exist_ok=True)
os.makedirs(os.path.dirname(tweets_csv_path), exist_ok=True)

# Initialize log file to clear previous contents
with open(log_file, 'w') as f:
    f.write(f"{formatted_time} - Log initialized\n")

def logging(message):
    """
    Logs messages to a specified log file with a timestamp.

    Args:
        message (str): The message to log.
    """
    now = datetime.now()
    formatted_time = now.strftime(time_format)
    with open(log_file, 'a') as f:
        f.write(f"{formatted_time} - {message}\n")

def load_processed_tweet_ids(file_path):
    """
    Load previously processed tweet IDs from a file.

    Args:
        file_path (str): Path to the file containing processed tweet IDs.

    Returns:
        set: A set of previously processed tweet IDs.
    """
    if os.path.exists(file_path):
        with open(file_path, 'r') as f:
            tweet_ids = set(line.strip() for line in f)
        logging(f"Loaded {len(tweet_ids)} processed tweet IDs from {file_path}")
        return tweet_ids
    logging(f"No processed tweet IDs file found at {file_path}, starting fresh.")
    return set()

def save_processed_tweet_ids(file_path, tweet_ids):
    """
    Save newly processed tweet IDs to a file.

    Args:
        file_path (str): Path to the file where tweet IDs will be saved.
        tweet_ids (set): Set of tweet IDs to be saved.
    """
    if tweet_ids:
        logging(f'Saving {len(tweet_ids)} new tweet IDs to {file_path}')
        with open(file_path, 'a') as f:
            for tweet_id in tweet_ids:
                f.write(f"{tweet_id}\n")
    else:
        logging('No new tweet IDs to save')
#%%       
# Extract Function
def extract_api(url="https://twitter-api45.p.rapidapi.com/search.php"):
    """
    Extracts tweets from the specified API endpoint and saves raw data to a JSON file.
    Includes retry logic for API communication failures.

    Args:
        url (str): The URL of the API endpoint to fetch tweets from.

    Returns:
        tuple: A tuple containing the list of new tweets and the path to the raw data file.
               Returns (None, None) if extraction fails.
    """
    logging('Starting the Extraction Phase')
    header = {
        "x-rapidapi-key": api_key,
        "x-rapidapi-host": api_host
    }

    querystring = {"query": "piggyvest", "search_type": "Top"}
    all_data = []
    next_cursor = None
    seen_tweet_ids = load_processed_tweet_ids(processed_tweet_ids_file)
    logging(f"Loaded {len(seen_tweet_ids)} previously processed tweet IDs")

    total_tweets_seen = 0
    total_tweets_dropped = 0
    new_tweet_ids = set()

    while True:
        if next_cursor:
            querystring['cursor'] = next_cursor
            logging(f'Getting records with cursor id: {next_cursor}')
        
        for retry in range(MAX_RETRIES):
            try:
                response = requests.get(url, headers=header, params=querystring)
                response.raise_for_status()
                logging('Successfully connected to the API')
                break
            except RequestException as e:
                logging(f"API request failed (attempt {retry + 1}/{MAX_RETRIES}): {str(e)}")
                if retry < MAX_RETRIES - 1:
                    logging(f"Retrying in {RETRY_DELAY} seconds...")
                    time.sleep(RETRY_DELAY)
                else:
                    logging("Max retries reached. Extraction failed.")
                    return None, None
        
        data = response.json()

        # Check if the data is empty or None
        if not data or 'timeline' not in data:
            logging('No data received from API or unexpected data format.')
            return None, None

        tweets_in_batch = len(data.get('timeline', []))
        total_tweets_seen += tweets_in_batch
        logging(f"Received {tweets_in_batch} tweets in this batch. Total tweets seen so far: {total_tweets_seen}")

        dropped_in_batch = 0
        for tweet in data.get('timeline', []):
            tweet_id = tweet.get('tweet_id')

            if tweet_id in seen_tweet_ids:
                dropped_in_batch += 1
                total_tweets_dropped += 1
                logging(f'Duplicate tweet found: {tweet_id}. Dropped.')
                continue

            seen_tweet_ids.add(tweet_id)
            new_tweet_ids.add(tweet_id)
            all_data.append(tweet)

        logging(f"Processed batch: {tweets_in_batch - dropped_in_batch} new tweets, {dropped_in_batch} duplicates dropped")
        
        next_cursor = data.get('next_cursor')
        if not next_cursor:
            logging('No more pages left to fetch')
            break
        else:
            logging(f'Moving to the next cursor: {next_cursor}')  # Log moving to the next cursor

    # Saving raw data
    if all_data:
        logging('Saving raw data to file')
        with open(raw_file_format, 'w') as json_file:
            json.dump(all_data, json_file, indent=4)
        logging(f'Successfully saved raw data to {raw_file_format}')
    else:
        logging('No data to save.')
    
    save_processed_tweet_ids(processed_tweet_ids_file, new_tweet_ids)
    logging(f'Added {len(new_tweet_ids)} new tweet IDs to processed_tweet_ids.txt')
    logging('Finished the Extraction Phase')
    return all_data, raw_file_format
#%%
# Transform Function
def transform(data):
    """
    Transforms the extracted tweet data into structured CSV files.

    Args:
        data (list): A list of dictionaries containing raw tweet data.

    Returns:
        tuple: A tuple containing the paths to the saved users and tweets CSV files.
               Returns (None, None) if transformation fails.
    """
    if data is None:
        logging('No data to transform. Exiting transformation phase.')
        return None, None

    logging('Starting the Transformation Phase')
     
    # URL extraction pattern
    url_pattern = re.compile(r'(https?://\S+)')

    # Creating empty lists
    user_details = []
    tweets = []
    logging('Initialized empty lists for user details and tweets')

    # Loop through the tweets
    for tweet in data:
        # Extract user details
        user_info = tweet['user_info']
        user_details.append({
            "display_name": user_info['screen_name'],
            "username": user_info['name'],
            "user_description": user_info.get('description', ''),
            "user_id": user_info['rest_id'],
            "followers_count": user_info['followers_count'],
            "favourites_count": user_info['favourites_count'],
            "avatar": user_info['avatar'],
            "is_verified": user_info['verified'],
            "following_count": user_info['friends_count']
        })

        # Extract hashtags
        hashtags = tweet.get('entities', {}).get('hashtags', [])
        hashtag_text = " ".join([hashtag['text'] for hashtag in hashtags]) if hashtags else ""

        # Extract URL from tweet text
        tweet_text = tweet['text']
        url_match = url_pattern.search(tweet_text)
        url = url_match.group(0) if url_match else None

        # Clean text logic
        # Remove mentions (@username) and URLs
        cleaned_text = re.sub(r'@\w+|http\S+', '', tweet_text)

        # Remove extra spaces
        cleaned_text = ' '.join(cleaned_text.split())

        # Remove non-alphanumeric characters except periods, commas, and apostrophes
        cleaned_text = re.sub(r'[^\w\s.,\']', '', cleaned_text)

        # Find all mentions (e.g., @username) in the original tweet text
        mentions = re.findall(r'@\w+', tweet_text)

        # Convert any emojis in the cleaned text to their text descriptions
        cleaned_text = emoji.demojize(cleaned_text)

        # Extract User Tweets
        tweet_info = {
            "tweet_id": tweet['tweet_id'],
            "user_id": user_info['rest_id'],
            "created_at": tweet['created_at'],
            "text": cleaned_text,
            "url": url,
            "mentions": " ".join(mentions).strip(),
            "lang": tweet['lang'],
            "favorites": tweet.get('favorites', 0),
            "retweets": tweet.get('retweets', 0),
            "replies": tweet.get('replies', 0),
            "quotes": tweet.get('quotes', 0),
            "view_count": tweet.get('views', 0),
            "hashtags": hashtag_text
        }
        tweets.append(tweet_info)

    logging('Finished extracting user details and tweets')
    
    # Converting lists to DataFrames
    logging('Converting lists to DataFrames')
    df_users = pd.DataFrame(user_details)
    df_users_tweet = pd.DataFrame(tweets)
    logging('Successfully converted lists to DataFrames')

    # Drop Duplicates User ID
    initial_user_count = len(df_users)
    df_users.drop_duplicates(subset=['user_id'], inplace=True)
    final_user_count = len(df_users)
    dropped_user_count = initial_user_count - final_user_count

    logging(f'{dropped_user_count} duplicate user IDs dropped out of {initial_user_count} total users.')

    # Drop rows where 'text' is empty or NaN
    initial_tweet_count = len(df_users_tweet)
    df_users_tweet.dropna(subset=['text'], inplace=True)
    df_users_tweet = df_users_tweet[df_users_tweet['text'].str.strip() != '']
    final_tweet_count = len(df_users_tweet)
    dropped_tweet_count = initial_tweet_count - final_tweet_count

    # Drop records where 'text' does not contain 'piggyvest'
    before_drop = len(df_users_tweet)
    df_users_tweet = df_users_tweet[df_users_tweet['text'].str.contains('piggyvest', case=False, na=False)]
    after_drop = len(df_users_tweet)

    records_dropped = before_drop - after_drop
    logging(f'Dropped {records_dropped} records because they did not contain "piggyvest".')
    
    logging(f'{dropped_tweet_count} tweets with empty or NaN text dropped out of {initial_tweet_count} total tweets.')

    # Save the DataFrame to CSV files
    logging('Saving DataFrames to CSV files')
    df_users.to_csv(users_csv_path, index=False)
    df_users_tweet.to_csv(tweets_csv_path, index=False)
    logging('Finished the Transformation Phase')
    logging(f'Successfully saved users data to {users_csv_path}')
    logging(f'Successfully saved tweets data to {tweets_csv_path}')

    return users_csv_path, tweets_csv_path
#%%
# load to s3 function
def load_to_s3(file_path, bucket_name, object_name=None):
    """
    Upload a file to an S3 bucket.

    Args:
        file_path (str): The path to the file to upload.
        bucket_name (str): The name of the S3 bucket.
        object_name (str, optional): The S3 object name. If not specified, the file name is used.

    Returns:
        bool: True if file was uploaded, else False.
    """
    logging(f'Starting the Load Phase for {file_path}')
    if object_name is None:
        object_name = os.path.basename(file_path)
    
    # Create an S3 client
    s3_client = boto3.client(
        's3',
        aws_access_key_id=ACCESS_KEY_ID,
        aws_secret_access_key=SECRET_ACCESS_KEY
    )
    try:
        s3_client.upload_file(file_path, bucket_name, object_name)
        logging(f'File {file_path} successfully uploaded to S3 bucket {bucket_name} as {object_name}')
        return True
    except ClientError as e:
        logging(f'Error uploading file to S3: {e}')
        return False
#%%    
# main function
def main():
    """
    Main function to orchestrate the ETL pipeline.

    This function coordinates the extraction, transformation, and loading phases of the pipeline.
    It handles any failures in each phase and ensures proper logging throughout the process.
    """
    logging('Starting the ETL pipeline')

    # Extraction
    raw_data, raw_data_path = extract_api()
    
    if raw_data is None:
        logging('Extraction failed. Exiting ETL pipeline.')
        return

    # Transformation
    users_csv, tweets_csv = transform(raw_data)
    
    if users_csv is None or tweets_csv is None:
        logging('Transformation failed. Exiting ETL pipeline.')
        return
    
    # Loading
    load_to_s3(raw_data_path, BUCKET_NAME, f'raw/{raw_file_name}')
    load_to_s3(users_csv, BUCKET_NAME, f'processed/users/users_{formatted_time}.csv')
    load_to_s3(tweets_csv, BUCKET_NAME, f'processed/users_tweet/tweets_{formatted_time}.csv')
    
    logging('Finished the ETL pipeline')


if __name__ == "__main__":
    main()

# %%
