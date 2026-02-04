def log_print(logger, message, level='info'):
    level = level.lower()

    if level == 'debug':
        logger.debug(message)
    elif level == 'warning':
        logger.warning(message)
    elif level == 'error':
        logger.error(message)
    elif level == 'critical':
        logger.critical(message)
    else:  # info por defecto
        logger.info(message)

    print(message)