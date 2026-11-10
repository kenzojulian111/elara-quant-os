"""
API Documentation (Swagger UI) setup via Flasgger.
Mengimplementasikan poin 10 dari saran Negatif Sir untuk developer portal & docs.
"""
import logging
from flask import Flask

log = logging.getLogger("elara.swagger")

def install_swagger_ui(app: Flask):
    """
    Mount Swagger UI at /apidocs.
    Reads Flask route docstrings to generate OpenAPI docs.
    """
    try:
        from flasgger import Swagger
    except ImportError:
        log.warning("flasgger not installed; Swagger API docs disabled. (run pip install flasgger)")
        return
    
    swagger_config = {
        "headers": [],
        "specs": [
            {
                "endpoint": 'apispec_1',
                "route": '/apispec_1.json',
                "rule_filter": lambda rule: True,  # all in
                "model_filter": lambda tag: True,  # all in
            }
        ],
        "static_url_path": "/flasgger_static",
        "swagger_ui": True,
        "specs_route": "/apidocs/"
    }

    template = {
        "swagger": "2.0",
        "info": {
            "title": "Elara API Data & Control",
            "description": "API Reference for Elara Core Services, Trading, and Smart Home Integrations.",
            "version": "2.0.0"
        },
        "securityDefinitions": {
            "Bearer": {
                "type": "apiKey",
                "name": "Authorization",
                "in": "header",
                "description": "JWT Authorization header using the Bearer scheme. Example: \\\"Authorization: Bearer {token}\\\""
            }
        },
        "security": [
            {
                "Bearer": []
            }
        ]
    }

    try:
        Swagger(app, config=swagger_config, template=template)
        log.info("Swagger UI API Docs mounted at /apidocs/")
    except Exception as e:
        log.error(f"Failed to mount Swagger UI: {e}")

