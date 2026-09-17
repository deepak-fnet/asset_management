---
description: Repository Information Overview
alwaysApply: true
---

# Vendor Management Information

## Summary
The **Vendor Management** project is a comprehensive Odoo module designed to manage the entire vendor lifecycle. It includes features for vendor categorization, document management, compliance tracking, contract handling, and performance evaluation. It also integrates with the Odoo portal to allow vendors to manage their own documents and requests.

## Structure
- **`models/`**: Contains the core business logic and database schema definitions for vendors, documents, performance, and more.
- **`views/`**: Defines the user interface, including form views, list views, and menus for both the backend and the portal.
- **`controllers/`**: Handles web routing and logic for the vendor portal.
- **`data/`**: Includes initial configuration data, email templates, sequences, and cron jobs.
- **`security/`**: Defines access control lists (ACLs) and record rules to ensure data security.

## Language & Runtime
**Language**: Python  
**Version**: 18.0 (Targeting Odoo 18.0)  
**Build System**: Odoo Module  
**Package Manager**: pip (Standard for Odoo environments)

## Dependencies
**Main Dependencies**:
- `base`: Core Odoo functionality.
- `mail`: For messaging and notification features.
- `portal`: For vendor-facing portal access.
- `website`: For web-based interactions.
- `contacts`: For managing partner data.
- `account`: For financial and accounting integrations.

## Build & Installation
To install this module in an Odoo environment, ensure it is in your Odoo `addons_path` and run the following command or install it via the Apps menu:

```bash
# Example command to install the module via CLI
./odoo-bin -c <config_file> -i vendor_management -d <database_name>
```

## Main Files & Resources
- **`__manifest__.py`**: The module manifest file containing metadata, dependencies, and data file declarations.
- **`models/vendor_management.py`**: Main logic for vendor management operations.
- **`views/vendor_menu.xml`**: Root menu definitions for the module.
- **`security/ir.model.access.csv`**: Main access right definitions.

## Testing
**Framework**: Odoo Testing Framework (based on unittest)
**Test Location**: Not explicitly found in the current repository, but typically resides in a `tests/` directory.

**Run Command**:
```bash
# Standard command to run tests for an Odoo module
./odoo-bin -c <config_file> -i vendor_management --test-enable -d <database_name> --stop-after-init
```
