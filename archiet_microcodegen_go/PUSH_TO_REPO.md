# Deploying to a separate Go repository

`go install` requires its own GitHub repository — it cannot be a subdirectory of a monorepo.

## Steps

1. Create a new repo at: https://github.com/aniekanasuquookono-web/archiet-microcodegen-go

2. Copy these files from the monorepo:
   ```
   archiet_microcodegen_go/main.go
   archiet_microcodegen_go/go.mod
   archiet_microcodegen_go/README.md
   ```

3. Push:
   ```bash
   git init
   git add .
   git commit -m "feat: archiet-microcodegen-go v0.1.0"
   git remote add origin https://github.com/aniekanasuquookono-web/archiet-microcodegen-go.git
   git push -u origin main
   git tag v0.1.0
   git push --tags
   ```

4. pkg.go.dev will auto-index within minutes of the first tag.

5. Users can then run:
   ```bash
   go install github.com/aniekanasuquookono-web/archiet-microcodegen-go@latest
   ```

## Note on go.mod module path

The `go.mod` declares:
```
module github.com/aniekanasuquookono-web/archiet-microcodegen-go
```

This must match the GitHub repo path exactly for `go install` to resolve it.
