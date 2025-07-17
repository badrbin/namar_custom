#!/bin/bash

# Script to fix namar_custom app build issues

echo "🔧 Fixing namar_custom app build configuration..."

# Create package.json
echo "📄 Creating package.json..."
cat > package.json << 'EOF'
{
  "name": "namar_custom",
  "version": "0.1.0",
  "description": "App مخصص لكشف حساب أوامر البيع والدفعات",
  "main": "index.js",
  "scripts": {
    "test": "echo \"Error: no test specified\" && exit 1"
  },
  "repository": {
    "type": "git",
    "url": ""
  },
  "author": "Your Name",
  "license": "MIT"
}
EOF

# Create pyproject.toml
echo "📄 Creating pyproject.toml..."
cat > pyproject.toml << 'EOF'
[build-system]
requires = ["flit_core >=3.4,<4"]
build-backend = "flit_core.buildapi"

[project]
name = "namar_custom"
version = "0.1.0"
description = "App مخصص لكشف حساب أوامر البيع والدفعات"
authors = [
    {name = "Your Name", email = "you@example.com"}
]
readme = "README.md"
requires-python = ">=3.10"
dependencies = [
    "frappe",
]

[project.urls]
Homepage = ""
Repository = ""
EOF

# Create app.json
echo "📄 Creating app.json..."
cat > app.json << 'EOF'
{
  "name": "namar_custom",
  "title": "Namar Custom",
  "description": "App مخصص لكشف حساب أوامر البيع والدفعات",
  "publisher": "Your Name",
  "email": "you@example.com",
  "icon": "octicon octicon-file-directory",
  "color": "grey",
  "version": "0.1.0",
  "license": "MIT"
}
EOF

# Remove placeholder.js if it exists
if [ -f "namar_custom/public/js/placeholder.js" ]; then
    echo "🗑️  Removing placeholder.js..."
    rm -f namar_custom/public/js/placeholder.js
fi

echo "✅ Configuration files created successfully!"
echo ""
echo "📝 Files created:"
ls -la package.json pyproject.toml app.json 2>/dev/null

echo ""
echo "🚀 Next steps:"
echo "1. Review the files to ensure they were created correctly"
echo "2. Run: git add ."
echo "3. Run: git commit -m 'Add required build configuration files'"
echo "4. Run: git push origin main"
