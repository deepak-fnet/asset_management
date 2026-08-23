# -*- coding: utf-8 -*-
{
    "name": "Remote Assistance",
    "version": "19.0.1.0.0",
    "summary": "Browser-based, consent-driven remote desktop assistance "
               "(Zoho-Assist style). Standalone.",
    "description": """
Remote Assistance
=================
A standalone module that lets an administrator securely connect to a
customer's computer *after* the customer explicitly consents.

Workflow
--------
1. Admin creates a Remote Assistance request (customer, reason, duration,
   machine type) and clicks Send Invitation.
2. The customer receives an email and clicks Accept or Reject.
3. On Accept, the customer is taken to a page to download a *temporary*
   Remote Assistance Agent (Windows .exe / Ubuntu .deb / portable script).
4. The customer runs the temporary agent. It authenticates, connects to the
   relay, and waits. It does NOT install a permanent background service.
5. The admin clicks Start Remote Session and controls the machine live from
   the browser. Either party can end the session at any time.

This module is completely standalone. It does NOT depend on Asset
Management, device inventory, or any pre-installed agent.
    """,
    "author": "FutureNet Technology",
    "category": "Tools",
    "license": "LGPL-3",
    "depends": ["base", "mail", "web"],
    "data": [
        "security/security.xml",
        "security/ir.model.access.csv",
        "data/sequence.xml",
        "data/config_params.xml",
        "data/mail_template.xml",
        "data/cron.xml",
        "views/remote_session_views.xml",
        "views/menu.xml",
        "views/recording_views.xml",
    ],
    "assets": {
        # The viewer and the public pages are standalone controller-rendered
        # pages, so their JS/CSS are referenced by the controller directly
        # rather than through an Odoo assets bundle.
    },
    "application": True,
    "installable": True,
}
