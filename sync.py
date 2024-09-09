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
            return xmlid_key
        else:
            print('Found an empty XML ID, skipping.')
    return None


def create_xmlid(client, model, record, complete_xmlid_name=None):
    """Create a new XML ID for a record in the specified model using the provided complete XML ID name."""
    if not complete_xmlid_name:
        # Generate a default name if none is provided
        module = '__migration__'
        complete_xmlid_name = f"{module}.{model.replace('.', '_')}_{record.id}"

    # Extract module and name from the provided complete XML ID
    parts = complete_xmlid_name.split('.', 1)
    if len(parts) < 2:
        print(f"Error: Invalid XML ID format '{complete_xmlid_name}'.")
        return None

    xmlid_module, xmlid_name = parts

    try:
        # Create a new XML ID in 'ir.model.data'
        client.env['ir.model.data'].create({
            'name': xmlid_name,  # Only use the actual name part
            'module': xmlid_module,
            'model': model,
            'res_id': record.id,
        })

        # Print and return the complete XML ID name
        print('New XML ID created:', complete_xmlid_name)
        return complete_xmlid_name
    except odoorpc.error.RPCError as e:
        print(f'RPCError while creating XML ID: {e}')
        return None


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


def get_field_type(model, field_name):
    """Retrieve the type of a field in a given model."""
    model_fields = model.fields_get([field_name])
    return model_fields[field_name]['type']


def sync_model(datas=None):
    """Synchronize models defined in the configuration file."""
    if not datas or 'models' not in datas:
        print('Invalid YAML data format.')
        return

    for data in datas['models']:
        source_model = data.get('source')
        target_model = data.get('target')
        data_name = data.get('name')
        if not source_model or not target_model:
            print("Source or target model missing in configuration.")
            continue

        while True:
            user_input = input(f"Do you want to continue with the source node '{data_name}'? (y/n/s): ").lower()
            if user_input in ['y', 'n', 's']:
                break
            print("Invalid input. Please enter 'y' to continue, 'n' to cancel, or 's' to skip this item.")

        if user_input == 'n':
            print("Operation canceled by the user.")
            break
        elif user_input == 's':
            print("Skipping this item.")
            continue

        try:
            # Get the records from the source model
            Records = odoo_source.env[source_model]
            # Apply filters if specified
            filters = data.get('filter', [])
            # Get limit from YAML, default to None if not specified
            limit = data.get('limit', None)
            if limit is not None:
                limit = int(limit)  # Ensure limit is an integer

            # Search records with filter and limit
            record_ids = Records.search(filters, limit=limit)
            if not record_ids:
                print(f"No records found for model '{source_model}' with the given filter and limit.")
                continue

            total_records = len(record_ids)  # Total number of records
            for index, record in enumerate(Records.browse(record_ids), start=1):
                print(f"Processing record {index}/{total_records} (ID: {record.id})")
                # Get the XML ID from the source (create if missing) and keep it for the target
                source_xmlid = get_xmlid(odoo_source, source_model, record)
                if not source_xmlid:
                    # Create the XML ID on the source
                    source_xmlid = create_xmlid(odoo_source, source_model, record)

                values = {}
                # Map fields from source to target
                for field in data.get('fields', []):
                    # Split source and target fields
                    if '>' in field:
                        field_source, field_target = field.split('>')
                    else:
                        field_source = field_target = field

                    # Get the field type
                    field_type = get_field_type(Records, field_source)

                    # Handle different field types
                    if field_type in ['char', 'text', 'selection']:
                        # Direct mapping for simple fields
                        values[field_target] = record[field_source]
                    elif field_type == 'many2one':
                        # Use XML ID for many2one fields
                        related_record = record[field_source]
                        if related_record:
                            related_xmlid = get_xmlid(odoo_source, related_record._name, related_record)
                            if not related_xmlid:
                                print(f"Error: Missing XML ID for related record {related_record.id} in model {related_record._name}. Skipping this field.")
                                continue  # Skip processing this field if XML ID is not found
                            values[field_target] = odoo_target.env.ref(related_xmlid).id
                    elif field_type == 'many2many':
                        # Use XML IDs for many2many fields
                        related_records = record[field_source]
                        related_ids = []
                        for related_record in related_records:
                            related_xmlid = get_xmlid(odoo_source, related_record._name, related_record)
                            if not related_xmlid:
                                print(f"Error: Missing XML ID for related record {related_record.id} in model {related_record._name}. Skipping this related record.")
                                continue  # Skip processing this related record if XML ID is not found
                            related_ids.append(odoo_target.env.ref(related_xmlid).id)
                        values[field_target] = [(6, 0, related_ids)]  # M2M update syntax

                # Create or update the record in the target instance
                create_or_update_record(odoo_target, target_model, source_xmlid, values)
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
