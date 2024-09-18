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
            print('Not found XML ID')
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


def apply_field_mapping(value, mapping):
    """Apply a mapping to a field value if a mapping is provided."""
    if mapping and value in mapping:
        return mapping[value]
    return value


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

        user_input = input(f"Do you want to continue with the source node '{data_name}'? (y/n): ")
        if user_input.lower() == 'n':
            print("Operation canceled by the user.")
            continue

        try:
            # Get the records from the source model
            Records = odoo_source.env[source_model]
            filters = data.get('filter', [])
            limit = data.get('limit', None)
            if limit is not None:
                limit = int(limit)

            # Search records with filter, limit, and order by ID ascending
            record_ids = Records.search(filters, limit=limit, order='id asc')

            if not record_ids:
                print(f"No records found for model '{source_model}' with the given filter and limit.")
                continue

            total_records = len(record_ids)
            for index, record in enumerate(Records.browse(record_ids), start=1):
                print(f"Processing record {index}/{total_records} (ID: {record.id})")
                source_xmlid = get_xmlid(odoo_source, source_model, record)
                if not source_xmlid:
                    source_xmlid = create_xmlid(odoo_source, source_model, record)

                values = {}
                field_mappings = data.get('field_mappings', {})  # Get field mappings if present

                for field in data.get('fields', []):
                    if '>' in field:
                        field_source, field_target = field.split('>')
                    else:
                        field_source = field_target = field

                    field_type = get_field_type(Records, field_source)

                    if field_type in ['char', 'text', 'selection']:
                        field_value = record[field_source]
                        mapping = field_mappings.get(field_source, {})

                        # Convert all line breaks to <br/> for any 'text' field
                        if isinstance(field_value, str):
                            field_value = field_value.replace('\r\n', '<br/>').replace('\n', '<br/>').replace('\r', '<br/>')

                        # Apply field mapping if it exists
                        values[field_target] = apply_field_mapping(field_value, mapping)

                    elif field_type == 'many2one':
                        # Handle many2one fields
                        related_record = record[field_source]
                        if related_record:
                            related_xmlid = get_xmlid(odoo_source, related_record._name, related_record)
                            if not related_xmlid:
                                print(f"Error: Missing XML ID for related record {related_record.id} in model {related_record._name}. Skipping this field.")
                                continue
                            values[field_target] = odoo_target.env.ref(related_xmlid).id
                    elif field_type == 'many2many':
                        # Handle many2many fields
                        related_records = record[field_source]
                        related_ids = []
                        for related_record in related_records:
                            related_xmlid = get_xmlid(odoo_source, related_record._name, related_record)
                            if not related_xmlid:
                                print(f"Warning: Missing XML ID for related record {related_record.id} in model {related_record._name}. Skipping this related record.")
                                continue  # Skip this related record if XML ID is missing
                            try:
                                # Attempt to resolve the XMLID to a record ID in the target instance
                                target_id = odoo_target.env.ref(related_xmlid).id
                                related_ids.append(target_id)
                            except odoorpc.error.RPCError as e:
                                # Log and continue if an error occurs while resolving XMLID
                                print(f"Error resolving XML ID '{related_xmlid}' in the target instance: {e}")
                                continue
                        # Set the many2many field with the list of resolved IDs
                        values[field_target] = [(6, 0, related_ids)]

                # create or update the record
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
