from app import create_app

app = create_app()

if __name__ == '__main__':
    # Run the Flask app on host 0.0.0.0 to be accessible from other machines
    app.run(host='0.0.0.0', port=5001, debug=True)


