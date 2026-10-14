# State

The bot keeps its memory here so it never posts or replies twice:

- `posted.json` - which posts from posts.csv have gone out
- `comments.json` - which comments it has already looked at

Don't edit these by hand unless you want to re-post something (delete that post's entry from `posted.json`).
