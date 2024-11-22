# -*- coding: utf-8 -*-
import sys
import yaml
from datetime import datetime
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
    """Get the XML ID for a record, if it exists, with special handling for product.template."""
    # Vérifier si le modèle est product.template
    if model == 'product.template':
        # Recherche d'une variante associée
        ProductVariant = client.env['product.product']
        variant_ids = ProductVariant.search([('product_tmpl_id', '=', record.id)])
        
        if variant_ids:
            variant = ProductVariant.browse(variant_ids[0])  # Prendre la première variante (si plusieurs existent)
            xmlid = variant.get_external_id()
            for xmlid_key in xmlid.values():
                if xmlid_key:
                    return xmlid_key
        else:
            print(f"No variants found for product.template ID {record.id}.")
            return None
    
    # Comportement standard pour les autres modèles
    xmlid = record.get_external_id()
    for xmlid_key in xmlid.values():
        if xmlid_key:
            return xmlid_key
        else:
            print('Not found XML ID')
    return None


def create_xmlid(client, model, record, complete_xmlid_name=None):
    """Create a new XML ID for a record in the specified model using the provided complete XML ID name."""
    if not complete_xmlid_name:
        module = '__migration__'
        complete_xmlid_name = f"{module}.{model.replace('.', '_')}_{record.id}"

    parts = complete_xmlid_name.split('.', 1)
    if len(parts) < 2:
        print(f"Error: Invalid XML ID format '{complete_xmlid_name}'.")
        return None

    xmlid_module, xmlid_name = parts

    client.env['ir.model.data'].create({
        'name': xmlid_name,
        'module': xmlid_module,
        'model': model,
        'res_id': record.id,
    })
    print('New XML ID created:', complete_xmlid_name)
    return complete_xmlid_name

def create_or_update_record(client, model, xmlid, values):
    """Create or update a record matching the XML ID with provided values."""
    try:
        record = client.env.ref(xmlid)
        record.write(values)
        return record.id
    except odoorpc.error.RPCError:
        model_obj = client.env[model]
        record_id = model_obj.create(values)
        create_xmlid(client, model, model_obj.browse(record_id), xmlid)
        return record_id

def get_field_type(model, field_name):
    """Retrieve the type of a field in a given model."""
    model_fields = model.fields_get([field_name])
    return model_fields[field_name]['type']

def process_char_field(record, field_name):
    """Process a char, text, or selection field."""
    field_value = record[field_name]
    if isinstance(field_value, str):
        field_value = field_value.replace('\r\n', '<br/>').replace('\n', '<br/>').replace('\r', '<br/>')
    return field_value

def process_many2one_field(odoo_source, odoo_target, record, field_name):
    """Process a many2one field and return the ID of the related record in the target instance."""
    related_record = record[field_name]
    if related_record:
        related_xmlid = get_xmlid(odoo_source, related_record._name, related_record)
        if not related_xmlid:
            print(f"Error: Missing XML ID for related record {related_record.id} in model {related_record._name}. Skipping this field.")
            return None
        return odoo_target.env.ref(related_xmlid).id
    return None

def process_many2many_field(odoo_source, odoo_target, record, field_name):
    """Process a many2many field and return the IDs of related records in the target instance."""
    related_records = record[field_name]
    related_ids = []
    for related_record in related_records:
        related_xmlid = get_xmlid(odoo_source, related_record._name, related_record)
        if not related_xmlid:
            print(f"Warning: Missing XML ID for related record {related_record.id} in model {related_record._name}. Skipping this related record.")
            continue
        target_id = odoo_target.env.ref(related_xmlid).id
        related_ids.append(target_id)
    return [(6, 0, related_ids)]

def process_boolean_field(record, field_name):
    """Process a boolean field."""
    return record[field_name]

def process_date_field(record, field_name):
    """Process a date field and return it in string format."""
    date_value = record[field_name]
    if date_value:
        return date_value.strftime('%Y-%m-%d')  # Odoo expects date strings in this format
    return None

def process_datetime_field(record, field_name):
    """Process a datetime field and return it in string format."""
    datetime_value = record[field_name]
    if datetime_value:
        return datetime_value.strftime('%Y-%m-%d %H:%M:%S')  # Odoo expects datetime strings in this format
    return None

def process_float_field(record, field_name):
    """Process a float field."""
    float_value = record[field_name]
    if float_value is not None:
        return float(float_value)
    return None

def sync_model(datas=None):
    """Synchronize models defined in the configuration file."""
    if not datas or 'models' not in datas:
        print('Invalid YAML data format.')
        return

    batch_size = 50  # Batch size

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

        Records = odoo_source.env[source_model]
        filters = data.get('filter', [])
        limit = data.get('limit', None)
        if limit is not None:
            limit = int(limit)

        record_ids = Records.search(filters, limit=limit, order='id asc')

        if not record_ids:
            print(f"No records found for model '{source_model}' with the given filter and limit.")
            continue

        total_records = len(record_ids)
        print(f"Total records found: {total_records}")
        
        field_mappings = data.get('field_mappings', {})

        for batch_start in range(0, total_records, batch_size):
            batch_end = min(batch_start + batch_size, total_records)
            batch_ids = record_ids[batch_start:batch_end]
            print(f"Processing batch {batch_start + 1} to {batch_end}...")

            for index, record in enumerate(Records.browse(batch_ids), start=batch_start + 1):
                print(f"Processing record {index}/{total_records} (ID: {record.id})")
                
                source_xmlid = get_xmlid(odoo_source, source_model, record)
                if not source_xmlid:
                    source_xmlid = create_xmlid(odoo_source, source_model, record)

                values = {}

                for field in data.get('fields', []):
                    if '>' in field:
                        field_source, field_target = field.split('>')
                    else:
                        field_source = field_target = field

                    field_type = get_field_type(Records, field_source)

                    # Apply field mapping if defined
                    if field_source in field_mappings:
                        mapping = field_mappings.get(field_source)
                        source_value = record[field_source]
                        if source_value in mapping:
                            values[field_target] = mapping[source_value]
                        else:
                            values[field_target] = record[field_source]
                    else:
                        # Normal field processing
                        if field_type in ['char', 'text', 'selection', 'integer']:
                            values[field_target] = process_char_field(record, field_source)
                        elif field_type == 'many2one':
                            related_id = process_many2one_field(odoo_source, odoo_target, record, field_source)
                            if related_id is not None:
                                values[field_target] = related_id
                        elif field_type == 'many2many':
                            related_ids = process_many2many_field(odoo_source, odoo_target, record, field_source)
                            if related_ids:
                                values[field_target] = related_ids
                        elif field_type == 'boolean':
                            values[field_target] = process_boolean_field(record, field_source)
                        elif field_type == 'date':
                            values[field_target] = process_date_field(record, field_source)
                        elif field_type == 'datetime':
                            values[field_target] = process_datetime_field(record, field_source)
                        elif field_type == 'float':
                            values[field_target] = process_float_field(record, field_source)

                create_or_update_record(odoo_target, target_model, source_xmlid, values)

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
