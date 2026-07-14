import os

class Config:
    # Basic Config
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'super-secret-default-key'
    
    # Database Config
    # Defaulting to a placeholder local PostgreSQL database
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'postgresql://postgres:postgres@localhost:5432/dashboard_db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
