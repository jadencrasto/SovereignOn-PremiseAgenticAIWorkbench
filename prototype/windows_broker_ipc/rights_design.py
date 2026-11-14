"""Candidate Windows pipe rights, for design checks only; not an ACL."""
FILE_READ_DATA = 0x0001
FILE_WRITE_DATA = 0x0002
FILE_CREATE_PIPE_INSTANCE = 0x0004
FILE_READ_ATTRIBUTES = 0x0080
FILE_WRITE_ATTRIBUTES = 0x0100
SYNCHRONIZE = 0x00100000

# Candidate client access, no generic access and no instance-creation bit.
# Exact rights and descriptor still require OS effective-access verification.
CLIENT_CANDIDATE = (FILE_READ_DATA | FILE_WRITE_DATA | FILE_READ_ATTRIBUTES |
                    FILE_WRITE_ATTRIBUTES | SYNCHRONIZE)
SERVER_INSTANCE_CREATE = FILE_CREATE_PIPE_INSTANCE
