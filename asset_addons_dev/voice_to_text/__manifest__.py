{
    'name': 'Voice to Text (Parakeet TDT)',
    'version': '19.0.1.0.0',
    'summary': 'Live microphone speech-to-text using NVIDIA Parakeet TDT',
    'description': """
Voice to Text
=============
Adds live microphone dictation backed by a locally-running NVIDIA Parakeet
TDT speech-to-text microservice.

Features:
    * "Voice to Text — Test Console" for testing dictation accuracy/latency,
      including fast speech.
    * Reusable `voice_dictation` widget for Char/Text fields.
    * Transcript log (text + timing only, no audio retained) for reviewing
      test runs.

Configuration (Settings > Voice to Text):
    * STT service URL
    * Chunk length (seconds)
    * Timeout
    * Enable / disable
    * Test Connection button
""",
    'author': 'Your Company',
    'category': 'Productivity',
    'depends': ['base', 'web'],
    'data': [
        'security/ir.model.access.csv',
        'views/res_config_settings_views.xml',
        'views/voice_transcript_log_views.xml',
        'views/voice_to_text_menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'voice_to_text/static/src/css/voice_to_text_console.css',
            'voice_to_text/static/src/js/voice_recorder.js',
            'voice_to_text/static/src/js/voice_to_text_console.js',
            'voice_to_text/static/src/xml/voice_to_text_console.xml',
            'voice_to_text/static/src/js/voice_dictation_widget.js',
            'voice_to_text/static/src/xml/voice_dictation_widget.xml',
        ],
    },
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}
