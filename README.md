# odoo-data-sync
This repository contains a Python script designed to synchronize data between two Odoo instances. The script connects to source and target Odoo databases using credentials from a configuration file (`config.ini`) and synchronizes models as defined in a YAML file. It supports creating or updating records, managing XML IDs, and mapping fields between different models.

## Key Features

- Connects to multiple Odoo instances using `odoorpc`.
- Reads model synchronization rules from a YAML file.
- Creates or updates records in the target instance based on data from the source instance.
- Automatically handles XML ID creation and management for seamless data synchronization.
- Provides a user-friendly interface with prompts to confirm actions.
