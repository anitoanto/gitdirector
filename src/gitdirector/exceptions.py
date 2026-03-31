class GitDirectorException(Exception):
    pass


class InvalidRepositoryError(GitDirectorException):
    pass


class RepositoryNotFoundError(GitDirectorException):
    pass


class ConfigurationError(GitDirectorException):
    pass


class GitOperationError(GitDirectorException):
    pass
