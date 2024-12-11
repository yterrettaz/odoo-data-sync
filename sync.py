# -*- coding: utf-8 -*-
import sys
import yaml
from datetime import datetime, date
import odoorpc
import traceback
from connect import *

def read_yaml_file(file_path):
    """Read a YAML file and return its contents."""
    try:
        with open(file_path, 'r') as file:
            return yaml.safe_load(file)
    except Exception as e:
        print(f"Error reading YAML file '{file_path}': {e}")
        raise

def get_xmlid(client, model, record, xmlid_mappings=None):
    """Get the XML ID for a record, considering XML ID mappings and special cases."""
    try:
        # Handle XML ID mappings
        if xmlid_mappings and isinstance(xmlid_mappings, dict):
            external_id = record.get_external_id()
            for xmlid_key in external_id.values():
                if xmlid_key and xmlid_key in xmlid_mappings:
                    return xmlid_mappings[xmlid_key]

        # # Special handling for product.template
        # if model == 'product.template':
        #     ProductVariant = client.env['product.product']
        #     variant_ids = ProductVariant.search([('product_tmpl_id', '=', record.id)])
        #     if variant_ids:
        #         variant = ProductVariant.browse(variant_ids[0])
        #         xmlid = variant.get_external_id()
        #         for xmlid_key in xmlid.values():
        #             if xmlid_key:
        #                 return xmlid_key
        #     else:
        #         print(f"No variants found for product.template ID {getattr(record, 'id', 'Unknown')}.")
        #         return None

        # Default XML ID retrieval
        xmlid = record.get_external_id()
        for xmlid_key in xmlid.values():
            if xmlid_key:
                return xmlid_key
        return None
    except Exception as e:
        print(f"Error retrieving XML ID for record {getattr(record, 'id', 'Unknown')} in model {model}: {e}")
        raise

def create_xmlid(client, model, record, complete_xmlid_name=None):
    """Create a new XML ID for a record in the specified model."""
    try:
        if not complete_xmlid_name:
            module = '__migration__'
            complete_xmlid_name = f"{module}.{model.replace('.', '_')}_{getattr(record, 'id', 'Unknown')}"

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
        return complete_xmlid_name
    except Exception as e:
        print(f"Error creating XML ID for record {getattr(record, 'id', 'Unknown')} in model {model}: {e}")
        sys.exit(1)  # Arrête immédiatement le script avec un code d'erreur

def create_or_update_record(client, model, xmlid, values):
    """
    Create or update a record based on the XML ID.
    If the XML ID exists, update the corresponding record.
    If not, create a new record and associate the XML ID.
    """
    # Check if the XML ID exists
    record = client.env['ir.model.data'].search([('module', '=', xmlid.split('.')[0]), ('name', '=', xmlid.split('.')[1])])
    
    if record:
        # If the XML ID exists, update the record
        record = client.env.ref(xmlid)
        record.write(values)
        return record.id
    else:
        # If the XML ID does not exist, create a new record
        model_obj = client.env[model]
        record_id = model_obj.create(values)
        new_record = model_obj.browse(record_id)
        # Link the new record to the XML ID
        create_xmlid(client, model, new_record, xmlid)
        return record_id

def sync_model(datas=None):
    """Synchronize models defined in the configuration file."""
    if not datas or 'models' not in datas:
        print("Invalid YAML data format.")
        return

    batch_size = 50  # Batch size

    for data in datas['models']:
        source_model = data.get('source')
        target_model = data.get('target')
        data_name = data.get('name')
        xmlid_mappings = data.get('xmlid_mappings', {})  # Load XML ID mappings from YAML
        if not source_model or not target_model:
            print("Source or target model missing in configuration.")
            sys.exit(1)  # Arrêter immédiatement

        Records = odoo_source.env[source_model]
        filters = data.get('filter', [])
        limit = data.get('limit', None)
        if limit is not None:
            limit = int(limit)

        try:
            record_ids = Records.search(filters, limit=limit, order='id asc')
        except Exception as e:
            print(f"Error searching records in model {source_model}: {e}")
            sys.exit(1)  # Arrêter immédiatement

        if not record_ids:
            print(f"No records found for model '{source_model}'.")
            sys.exit(1)  # Arrêter immédiatement
        
        total_records = len(record_ids)
        print(f"Total records found: {total_records}")
        
        for batch_start in range(0, total_records, batch_size):
            batch_end = min(batch_start + batch_size, total_records)
            batch_ids = record_ids[batch_start:batch_end]
            print(f"Processing batch {batch_start + 1} to {batch_end}...")

            for index, record in enumerate(Records.browse(batch_ids), start=batch_start + 1):
                record_id = getattr(record, 'id', 'Unknown')
                print(f"Processing record {index}/{total_records} (ID: {record_id})")
                
                try:
                    source_xmlid = get_xmlid(odoo_source, source_model, record, xmlid_mappings)
                    if not source_xmlid:
                        source_xmlid = create_xmlid(odoo_source, source_model, record)

                    values = {}

                    for field in data.get('fields', []):
                        if '>' in field:
                            field_source, field_target = field.split('>')
                        else:
                            field_source = field_target = field

                        source_field_type = Records.fields_get([field_source])[field_source]['type']
                        target_field_type = odoo_target.env[target_model].fields_get([field_target])[field_target]['type']

                        raw_value = record[field_source]

                        # Apply field mapping if defined
                        field_mappings = data.get('field_mappings', {})
                        if field_target in field_mappings:
                            mapping = field_mappings[field_target]
                            if raw_value in mapping:
                                raw_value = mapping[raw_value]
                        if source_field_type in ['char', 'selection', 'integer', 'float', 'date', 'datetime', 'boolean', 'text']:
                            if source_field_type in ['date', 'datetime'] and raw_value:
                                if isinstance(raw_value, datetime):
                                    # Si c'est une instance de datetime, formatez-la
                                    raw_value = raw_value.strftime('%Y-%m-%d %H:%M:%S')
                                elif isinstance(raw_value, date):
                                    # Si c'est une instance de date, formatez-la
                                    raw_value = raw_value.strftime('%Y-%m-%d')
                                elif isinstance(raw_value, str) and 'T' in raw_value:
                                    # Si c'est une chaîne ISO 8601
                                    raw_value = raw_value.replace('T', ' ')
                                values[field_target] = raw_value
                            elif source_field_type == 'text' and target_field_type == 'html' and raw_value:
                                # Remplacer les retours à la ligne par des balises <br>
                                raw_value = raw_value.replace('\n', '<br>')
                            values[field_target] = raw_value
                        elif source_field_type == 'many2one':
                            related_record = record[field_source]
                            if related_record:
                                try:
                                    related_xmlid = get_xmlid(odoo_source, related_record._name, related_record, xmlid_mappings)
                                    if related_xmlid:
                                        try:
                                            values[field_target] = odoo_target.env.ref(related_xmlid).id
                                        except odoorpc.error.RPCError as e:
                                            print(f"Error finding related record in target for XML ID {related_xmlid}: {e}")
                                            traceback.print_exc()
                                            sys.exit(1)  # Arrêter immédiatement
                                    else:
                                        print(f"Missing XML ID for related record {getattr(related_record, 'id', 'Unknown')}.")
                                except Exception as e:
                                    print(f"Error processing related record {getattr(related_record, 'id', 'Unknown')}: {e}")
                                    traceback.print_exc()
                                    sys.exit(1)  # Arrêter immédiatement
                        elif source_field_type == 'many2many':
                            related_records = record[field_source]
                            related_ids = []
                            for related_record in related_records:
                                related_xmlid = get_xmlid(odoo_source, related_record._name, related_record, xmlid_mappings)
                                if related_xmlid:
                                    try:
                                        related_ids.append(odoo_target.env.ref(related_xmlid).id)
                                    except odoorpc.error.RPCError:
                                        print(f"Missing target record for XML ID {related_xmlid}.")
                                else:
                                    print(f"Warning: Missing XML ID for related record {getattr(related_record, 'id', 'Unknown')}.")
                            values[field_target] = [(6, 0, related_ids)]
                        else:
                            print(f"Unsupported field type: {source_field_type}")

                    create_or_update_record(odoo_target, target_model, source_xmlid, values)
                except Exception as e:
                    print(f"Error processing record ID {record_id}: {e}")
                    traceback.print_exc()  # Print full error traceback
                    sys.exit(1)  # Arrêter immédiatement

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Please specify the path to the YAML file as an argument.")
    else:
        yaml_file_path = sys.argv[1]
        try:
            datas = read_yaml_file(yaml_file_path)
            sync_model(datas)
        except yaml.YAMLError as e:
            print(f"YAML error: {e}")
        except FileNotFoundError:
            print("The specified YAML file was not found.")
        except Exception as e:
            print(f"Unexpected error: {e}")
            traceback.print_exc()
