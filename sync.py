# -*- coding: utf-8 -*-
import sys
import yaml
from backports import configparser
import odoorpc

# Settings from config.ini
config = configparser.ConfigParser()
config.read('config.ini')


# Connect to an Odoo instance
def connect_instance(instance):
    """Connect to an Odoo instance using credentials from the config file."""
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


# Declare instances
odoo_source = connect_instance('source')
odoo_target = connect_instance('target')


def read_yaml_file(file_path):
    """Read a YAML file and return its contents."""
    with open(file_path, 'r') as file:
        return yaml.safe_load(file)


def get_xmlid(client, model, record):
    """Get the XML ID for a record, if it exists."""
    xmlid = record.get_external_id()
    for xmlid_key in xmlid.values():
        if xmlid_key:  # Ensure the XML ID is not empty
            print('Found XML ID:', xmlid_key)
            return xmlid_key
        else:
            print('Found an empty XML ID, skipping.')
    return None


def create_xmlid(client, model, record, name=False):
    """Create a new XML ID for a record in the specified model."""
    module = '__migration__'

    # Determine the name for the XML ID
    if not name:
        name = f"{model.replace('.', '_')}_{record.id}"
    elif not name.startswith(f"{module}."):
        name = f"{module}.{name}"

    # Create a new XML ID in 'ir.model.data'
    new_record_id = client.env['ir.model.data'].create({
        'name': name.split('.')[-1],  # Only use the actual name without the module prefix
        'module': module,
        'model': model,
        'res_id': record.id,
    })

    # Retrieve and print the complete XML ID name
    complete_xmlid_name = client.env['ir.model.data'].browse(new_record_id).complete_name
    print('New XML ID created:', complete_xmlid_name)
    return complete_xmlid_name


def create_or_update_record(client, model, xmlid, values):
    """Create or update a record matching the XML ID with provided values."""
    try:
        # Attempt to find the record by its XML ID
        record = client.env.ref(xmlid)
        # If the record exists, update it
        record.write(values)
        return record.id
    except odoorpc.error.RPCError:
        # If the XML ID does not exist, create a new record
        model_obj = client.env[model]
        record_id = model_obj.create(values)
        # Create or retrieve an XML ID for the new record
        create_xmlid(client, model, model_obj.browse(record_id), xmlid)
        return record_id


def sync_model(datas=None):
    """Synchronize models defined in the configuration file."""
    for data in datas['models']:
        user_input = input(f"Do you want to continue with the source node '{data['source']}'? (y/n): ")
        if user_input.lower() != 'y':
            print("Operation canceled by the user.")
            continue

        try:
            # Get the records from the source model
            Records = odoo_source.env[data['source']]
            record_ids = Records.search([], limit=10)

            for record in Records.browse(record_ids):
                # Get the XML ID from the source (create if missing) and keep it for the target
                source_xmlid = get_xmlid(odoo_source, data['source'], record)
                if not source_xmlid:
                    source_xmlid = create_xmlid(odoo_source, data['source'], record)

                values = {}
                # Map fields from source to target
                for field in data['fields']:
                    if '>' in field:
                        field_source, field_target = field.split('>')
                        values[field_target] = record[field_source]
                    else:
                        values[field] = record[field]

                # Create or update the record in the target instance
                create_or_update_record(odoo_target, data['target'], source_xmlid, values)
        except odoorpc.error.RPCError as e:
            print(f'RPCError: {e}')
        except Exception as e:
            print(f'Unexpected error: {e}')


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Please specify the path to the YAML file as an argument.")
    else:
        yaml_file_path = sys.argv[1]
        try:
            datas = read_yaml_file(yaml_file_path)
            sync_model(datas)
        except yaml.YAMLError as e:
            print(f'YAML error: {e}')
        except FileNotFoundError:
            print("The specified YAML file was not found.")
        except Exception as e:
            print(f'Unexpected error: {e}')
