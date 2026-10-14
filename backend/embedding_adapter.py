"""Small JSON adapters for internal services. A URL alone does not identify a schema."""
import json


def build_request(config, texts):
    if config.api_format == "company":
        if len(texts) != 1:
            raise ValueError("The confirmed company contract accepts one input string")
        return {"input": texts[0], "model": config.service.model}
    if config.api_format == "openai_compatible":
        return {"model": config.service.model, "input": texts, "encoding_format": "float"}
    if config.api_format == "tei":
        return {"inputs": texts, "truncate": False}
    template = json.loads(config.contract_json)["request"]

    def replace(value):
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace(item) for item in value]
        if value == "$texts":
            return texts
        if value == "$model":
            return config.service.model
        if value == "$text":
            if len(texts) != 1:
                raise ValueError("Single-text contracts require EMBEDDING_BATCH_SIZE=1")
            return texts[0]
        return value

    return replace(template)


def parse_response(config, payload, count):
    if config.api_format == "company":
        contract = {"items_path": ["data"], "vector_path": ["embedding"]}
    elif config.api_format == "openai_compatible":
        contract = {"items_path": ["data"], "vector_path": ["embedding"], "index_field": "index"}
    elif config.api_format == "tei":
        contract = {"items_path": [], "vector_path": [], "index_field": None}
    else:
        contract = json.loads(config.contract_json)["response"]

    def at(value, path):
        for key in path:
            value = value[key]
        return value

    items = at(payload, contract["items_path"])
    if contract.get("single_vector"):
        if count != 1:
            raise ValueError("Single-vector responses require one input")
        return [at(items, contract["vector_path"])]
    if not isinstance(items, list) or len(items) != count:
        raise ValueError("Response count does not match input count")
    index = contract.get("index_field")
    if index:
        if any(type(item.get(index)) is not int for item in items) or sorted(item[index] for item in items) != list(range(count)):
            raise ValueError("Invalid response indexes")
        items = sorted(items, key=lambda x: x[index])
    return [at(item, contract["vector_path"]) for item in items]
