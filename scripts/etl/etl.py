"""
Sentiment Analysis Pipeline

This script performs an ETL (Extract, Transform, Load) process on Twitter data. It includes:
- Extraction of tweets from the Twitter API
- Transformation of raw tweet data into structured CSV files
- Uploading of raw and processed data to an AWS S3 bucket

Dependencies:
- configparser
- requests
- pandas
- json
- datetime
- hashlib
- re
- emoji
- textblob
- nltk
- boto3
"""

# Import Libraries
import configparser
import requests
import pandas as pd
import json
from datetime import datetime
import hashlib
import re
import emoji
from textblob import Word
from nltk.corpus import stopwords
from nltk.tokenize import word_tokenize
import nltk
import boto3
import os
from botocore.exceptions import ClientError

# Download the stopwords dataset and the punkt tokenizer models from NLTK
nltk.download('stopwords')
nltk.download('punkt')

# Load my secret file
config = configparser.ConfigParser()
config.read('config/secrets.ini')

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
raw_file_path = 'data/raw/'
raw_file_name = f'raw_data_{formatted_time}.json'
raw_file_format = raw_file_path + raw_file_name
log_file_path = 'logs/'
log_file = os.path.join(log_file_path, 'etl_log.txt')
processed_hashes_file = os.path.join(log_file_path, 'processed_tweet_hashes.txt')

# CSV file paths
users_csv_path = f'data/processed/users/users_{formatted_time}.csv'
tweets_csv_path = f'data/processed/users_tweet/tweets_{formatted_time}.csv'

# Initialize log file to clear previous contents
with open(log_file, 'w') as f:
    f.write(f"{formatted_time} - Log initialized\n")

# logging Function
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

# load_processed_hashes Function
def load_processed_hashes(file_path):
    """
    Loads processed tweet hashes from a file.

    Args:
        file_path (str): The path to the file containing processed tweet hashes.

    Returns:
        set: A set of tweet hashes that have been processed.
    """
    if os.path.exists(file_path):
        with open(file_path, 'r') as f:
            return set(line.strip() for line in f)
    return set()

# save_processed_hashes Function
def save_processed_hashes(file_path, hashes):
    """
    Saves new tweet hashes to a file.

    Args:
        file_path (str): The path to the file to save processed tweet hashes.
        hashes (set): A set of tweet hashes to save.
    """
    if hashes:
        logging(f'Saving {len(hashes)} new hashes to {file_path}')
        with open(file_path, 'a') as f:
            for _hash in hashes:
                f.write(f"{_hash}\n")
    else:
        logging('No new hashes to save')
# Extract_API Function
def extract_api(url="https://twitter-api45.p.rapidapi.com/search.php"):
    """
    Extracts tweets from the specified API endpoint and saves raw data to a JSON file.

    Args:
        url (str): The URL of the API endpoint to fetch tweets from.

    Returns:
        tuple: A tuple containing the extracted data and the path to the saved raw data file.
    """
    logging('Starting the Extraction Phase')
    header = {
        "x-rapidapi-key": api_key,
        "x-rapidapi-host": api_host
    }

    querystring = {"query": "air peace", "search_type": "Top"}
    all_data = []
    next_cursor = None
    seen_tweets = load_processed_hashes(processed_hashes_file)  # Load previously processed tweet hashes

    while True:
        if next_cursor:
            querystring['cursor'] = next_cursor
            logging(f'Getting records with cursor id: {next_cursor}')  # Log the current cursor ID
        response = requests.get(url, headers=header, params=querystring)
        if response.status_code == 200:
            logging('Successfully connected to the API')
            data = response.json()

            # Deduplicate tweets
            new_hashes = set()
            for tweet in data.get('timeline', []):
                tweet_id = tweet.get('tweet_id')
                tweet_content = tweet.get('text', '')
                tweet_hash = hashlib.md5(f"{tweet_id}:{tweet_content}".encode()).hexdigest()

                if tweet_hash not in seen_tweets:
                    seen_tweets.add(tweet_hash)
                    new_hashes.add(tweet_hash)
                    all_data.append(tweet)
                else:
                    logging(f'Duplicate tweet found: {tweet_id}')

            # Check for next cursor
            next_cursor = data.get('next_cursor')
            if not next_cursor:
                logging('No more pages left to fetch')
                break
            else:
                logging(f'Moving to the next cursor: {next_cursor}')  # Log moving to the next cursor
        else:
            logging(f'Failed to establish connection, status code: {response.status_code}')
            break

    # Saving raw data
    logging('Saving raw data to file')
    with open(raw_file_format, 'w') as json_file:
        json.dump(all_data, json_file, indent=4)
    logging(f'Successfully saved raw data to {raw_file_format}')

    save_processed_hashes(processed_hashes_file, new_hashes)  # Save new tweet hashes
    logging('Finished the Extraction Phase')
    return all_data, raw_file_format

# Transform Function
def transform(data):
    """
    Transforms the extracted tweet data into structured CSV files.

    Args:
        data (list): A list of raw tweet data.

    Returns:
        tuple: A tuple containing the paths to the saved users and tweets CSV files.
    """
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

        # Find all mentions (e.g., @username) in the tweet text
        mentions = re.findall(r'@\w+', tweet_text)

        # Convert the tweet text to lowercase
        cleaned_text = tweet_text.lower()

        # Remove mentions (@username), hashtags (#hashtag), URLs, and non-word characters (punctuation, etc.)
        cleaned_text = re.sub(r'@\w+|#\w+|http\S+|[^\w\s]', '', cleaned_text)

        # Remove extra spaces by splitting the text into words and joining them back with a single space
        cleaned_text = ' '.join(cleaned_text.split())

        # Tokenize the cleaned text into individual words
        tokens = word_tokenize(cleaned_text)

        # Define a set of English stop words (common words that are usually removed in text processing)
        stop_words = set(stopwords.words('english'))

        # Remove stop words and words shorter than 3 characters from the tokens list
        tokens = [word for word in tokens if word not in stop_words and len(word) > 2]

        # Correct the spelling of each word in the tokens list
        tokens = [Word(word).correct() for word in tokens]

        # Join the tokens back into a single string
        cleaned_text = ' '.join(tokens)

        # Convert any emojis in the cleaned text to their text descriptions (e.g., ":smile:")
        cleaned_text = emoji.demojize(cleaned_text)

        # Logging the number of tweets transformed
        logging(f'{len(tweets)} tweets transformed successfully.')


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
            "views": tweet.get('views', 0),
            "hashtags": hashtag_text
        }
        tweets.append(tweet_info)

    logging('Finished extracting user details and tweets')
    
    # Converting lists to DataFrames
    logging('Converting lists to DataFrames')
    df_users = pd.DataFrame(user_details)
    df_users_tweet = pd.DataFrame(tweets)
    logging('Successfully converted lists to DataFrames')

    # Drop duplicate user IDs
    initial_user_count = len(df_users)  # Get initial count of users
    df_users.drop_duplicates(subset=['user_id'], inplace=True)  # Drop duplicate user IDs
    final_user_count = len(df_users)  # Get final count of users after dropping duplicates
    dropped_user_count = initial_user_count - final_user_count  # Calculate the number of dropped user IDs

    # Logging the number of duplicate user IDs dropped
    logging(f'{dropped_user_count} duplicate user IDs dropped out of {initial_user_count} total users.')

    # Drop rows where 'text' is empty or NaN
    initial_tweet_count = len(df_users_tweet)  # Get initial count of tweets
    df_users_tweet.dropna(subset=['text'], inplace=True)  # Drop rows with empty or NaN text
    df_users_tweet = df_users_tweet[df_users_tweet['text'].str.strip() != '']  # Drop rows where 'text' is empty after stripping spaces
    final_tweet_count = len(df_users_tweet)  # Get final count of tweets after dropping empty text
    dropped_tweet_count = initial_tweet_count - final_tweet_count  # Calculate the number of dropped tweets

    # Logging the number of tweets dropped due to empty or NaN text
    logging(f'{dropped_tweet_count} tweets with empty or NaN text dropped out of {initial_tweet_count} total tweets.')


    # Save the DataFrame to CSV files
    logging('Saving DataFrames to CSV files')
    df_users.to_csv(users_csv_path, index=False)
    df_users_tweet.to_csv(tweets_csv_path, index=False)
    logging('Finished the Transformation Phase')
    logging(f'Successfully saved users data to {users_csv_path}')
    logging(f'Successfully saved tweets data to {tweets_csv_path}')
    
    return users_csv_path, tweets_csv_path

# load_to_s3 Function
def load_to_s3(file_path, bucket_name, object_name=None):
    """
    Uploads a file to an AWS S3 bucket.

    Args:
        file_path (str): The path to the file to upload.
        bucket_name (str): The name of the S3 bucket to upload to.
        object_name (str, optional): The S3 object name. Defaults to None.

    Returns:
        bool: True if the file was uploaded successfully, False otherwise.
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

# load_to_s3 Function
def main():
    """
    The main function that orchestrates the ETL process.
    """
    logging('Starting the ETL pipeline')

    # Extraction
    raw_data, raw_data_path = extract_api()
    
    # Transformation
    users_csv, tweets_csv = transform(raw_data)
    
    # Loading
    load_to_s3(raw_data_path, BUCKET_NAME, f'raw/{raw_file_name}')
    load_to_s3(users_csv, BUCKET_NAME, f'processed/users/users_{formatted_time}.csv')
    load_to_s3(tweets_csv, BUCKET_NAME, f'processed/users_tweet/tweets_{formatted_time}.csv')
    
    logging('Finished the ETL pipeline')

if __name__ == "__main__":
    main()
