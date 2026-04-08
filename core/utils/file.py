import json


def read_file(filename):
    if filename.endswith(".MD"):
        return open(filename, "r", encoding="utf-8").read()
    elif filename.endswith(".json"):
        return json.load(open(filename))
    else:
        raise Exception("File extension not supported")
