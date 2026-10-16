from . import models
from . import controllers


def post_init_hook(env):
    env['dms.folder']._sync_department_folders()
