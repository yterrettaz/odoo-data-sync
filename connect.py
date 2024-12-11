# -*- coding: utf-8 -*-
import odoorpc
from backports import configparser

# Connect to an Odoo instance
def connect_instance(instance):
    """Connect to an Odoo instance using credentials from the config file."""
    try:
        odoo_instance = odoorpc.ODOO(
            config.get(instance, 'host'),
            port=config.getint(instance, 'port'),
            protocol=config.get(instance, 'protocol')
        )

        odoo_instance.login(
            config.get(instance, 'database'),
            config.get(instance, 'user'),
            config.get(instance, 'password')
        )
        return odoo_instance
    except Exception as e:
        print(f"Error connecting to instance '{instance}': {e}")
        raise

# Settings from config.ini
config = configparser.ConfigParser()
config.read('config.ini')

# Declare instances
odoo_source = connect_instance('source')
odoo_target = connect_instance('target')

