{
    'name': 'Asset Depreciation to Accounting',
    'version': '19.0.1.2.0',
    'summary': "Calculate Depreciation on an asset also creates / updates its accounting asset "
               "(account.asset.asset) and computes the depreciation board",
    'category': 'Accounting',
    'depends': ['general_asset', 'base_accounting_kit'],
    'data': [
        'views/views.xml',
    ],
    'author': 'Futurenet',
    'license': 'LGPL-3',
    'installable': True,
}
