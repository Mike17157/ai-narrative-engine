"""Command-line entry point for migrating legacy story storage."""

from loom.server.services.story_migration import *  # noqa: F403

if __name__ == "__main__":
    main()
