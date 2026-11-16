from epadata import create_app, globals


def main():
    create_app()
    globals.app.run(debug=True, port=5001)

if __name__ == "__main__":
    main()
