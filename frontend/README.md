# Astrolift UI

Web dashboard for the Astrolift platform. Built with Next.js 16 (App Router),
TypeScript, Apollo Client, Tailwind CSS, and shadcn/ui.

## Quick start

```bash
npm install
cp .env.example .env   # then fill in values
npm run dev             # http://localhost:3000
```

## Commands

| Command              | Description                    |
|----------------------|--------------------------------|
| `make dev`           | Start dev server               |
| `make build`         | Production build               |
| `make lint`          | Run ESLint                     |
| `make fmt`           | Format with Prettier           |
| `make fmt-check`     | Check formatting (CI)          |
| `make codegen`       | Generate GraphQL types         |
| `make clean`         | Remove build artifacts         |

See [`bootstrap.md`](bootstrap.md) for full conventions and architecture.
