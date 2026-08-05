#!/usr/bin/env ruby
# frozen_string_literal: true
# archiet-microcodegen-rails v0.1.0
# PRD text -> Rails 7 API app -> ZIP. Pure Ruby stdlib. <1400 LOC.
#
# Stage 1: parse_prd(text)              -> manifest (language-agnostic)
# Stage 2: manifest_to_genome(manifest) -> genome   (ArchiMate 3.2 typed)
# Stage 3: render_genome(genome)        -> {path => content} (Rails-specific)
# Stage 4: pack_zip(files)              -> bytes (PKZIP, pure Zlib::Deflate)
#
# Zero runtime dependencies. Inspired by Karpathy's micrograd.

require 'zlib'
require 'stringio'
require 'fileutils'
require 'json'
require 'optparse'

# ─── ZIP WRITER ──────────────────────────────────────────────────────────────
CRC_TABLE = Array.new(256) { |i|
  c = i
  8.times { c = (c & 1) == 1 ? (0xEDB88320 ^ (c >> 1)) : (c >> 1) }
  c
}.freeze

def crc32(data)
  crc = 0xFFFFFFFF
  data.each_byte { |b| crc = (CRC_TABLE[(crc ^ b) & 0xFF] ^ (crc >> 8)) & 0xFFFFFFFF }
  (crc ^ 0xFFFFFFFF) & 0xFFFFFFFF
end

def deflate_raw(data)
  z = Zlib::Deflate.new(Zlib::DEFAULT_COMPRESSION, -15)
  out = z.deflate(data.b, Zlib::FINISH)
  z.close
  out
end

def pack_zip(files)
  out = ''.b
  cd  = ''.b
  off = 0
  files.sort_by { |p, _| p }.each do |path, content|
    raw  = content.encode('UTF-8', invalid: :replace, undef: :replace).b
    comp = deflate_raw(raw)
    crc  = crc32(raw)
    name = path.b
    lh   = [0x04034b50, 20, 0, 8, 0, 0, crc, comp.bytesize, raw.bytesize,
            name.bytesize, 0].pack('VvvvvvVVVvv') + name
    out += lh + comp
    cd  += [0x02014b50, 20, 20, 0, 8, 0, 0, crc, comp.bytesize, raw.bytesize,
            name.bytesize, 0, 0, 0, 0, 0, off].pack('VvvvvvvVVVvvvvvVV') + name
    off += lh.bytesize + comp.bytesize
  end
  n = files.size; cdl = cd.bytesize
  out + cd + [0x06054b50, 0, 0, n, n, cdl, off, 0].pack('VvvvvVVv')
end

def write_disk(files, base)
  files.each do |path, content|
    full = File.join(base, path)
    FileUtils.mkdir_p(File.dirname(full))
    File.write(full, content)
  end
  puts "Wrote #{files.size} files to #{base}"
end

# ─── HELPERS ─────────────────────────────────────────────────────────────────
def pascal(s)
  s.gsub(/[_-]([a-zA-Z0-9])/) { $1.upcase }.sub(/^./) { $&.upcase }
end
def snake(s)
  s.gsub(/([A-Z]+)([A-Z][a-z])/, '\1_\2')
   .gsub(/([a-z\d])([A-Z])/, '\1_\2')
   .downcase
end
def plural(s)
  s.end_with?('y') && !'aeiou'.include?(s[-2]) ? s[0..-2] + 'ies' :
  s.match?(/[sxz]$/) ? s + 'es' : s + 's'
end
def fill(tmpl, vars)
  vars.each { |k, v| tmpl = tmpl.gsub("{{#{k}}}", v.to_s) }
  tmpl
end

# ─── STAGE 1: parse_prd ──────────────────────────────────────────────────────
def parse_prd(text)
  name = text.match(/^#\s+(.+)/)&.captures&.first&.strip || 'MyApp'
  entities = []
  if (m = text.match(/^[#]{1,3}\s*(?:entities|data models|domain models)[^\n]*/i))
    sec = text[m.begin(0)..]
    sec = sec[0, sec.index(/\n[#]{1,3}\s+(?!entities|data|domain)/i) || sec.length]
    sec.scan(/^[\s\-\*]*([A-Z][a-zA-Z0-9]{1,40})\*{0,2}[ \t]*(?::|—|-| )/) do |(en)|
      next if %w[User Auth Admin Api].include?(en)
      fields = []
      epos   = sec.index(en) || 0
      sec[epos, 600].scan(/^\s+[-*]\s*([a-z_][a-z0-9_]{0,40})\s*[:—]\s*([a-zA-Z]+)([^\n]*)/) do |(fn, ft, rest)|
        fields << { name: fn, type: ft.downcase,
                    required: rest.downcase.include?('required') || rest.include?('*') }
      end
      entities << { name: en, fields: fields }
    end
  end
  stories = text.scan(/As a[n]?\s+\w+,\s*I want[^.\n]+/i).map(&:strip)
  integrations = %w[stripe sendgrid twilio slack github google aws s3 cloudinary firebase].select { |k| text.downcase.include?(k) }
  { name: name, entities: entities, stories: stories, integrations: integrations }
end

# ─── STAGE 2: manifest_to_genome ─────────────────────────────────────────────
def manifest_to_genome(m)
  slug    = m[:name].downcase.gsub(/[^a-z0-9]+/, '-').sub(/-+$/, '')
  modules = m[:entities].map do |e|
    base_fields = [
      { name: 'id',         type: 'bigint',    required: true },
      { name: 'user_id',    type: 'bigint',    required: true },
      { name: 'created_at', type: 'timestamp', required: false },
      { name: 'updated_at', type: 'timestamp', required: false },
    ]
    { name: e[:name], archimate: 'DataObject', fields: base_fields + e[:fields] }
  end
  { solution_name: m[:name], slug: slug, version: '0.1.0', language: 'rails',
    auth: { strategy: 'jwt', storage: 'httponly_cookie' },
    modules: modules, integrations: m[:integrations], user_stories: m[:stories] }
end

# ─── STAGE 3: render_genome ──────────────────────────────────────────────────
def render_genome(g)
  files = {}
  name  = g[:solution_name]
  slug  = g[:slug]
  mods  = g[:modules]

  # Gemfile
  files['Gemfile'] = <<~GEMFILE
    source 'https://rubygems.org'
    ruby '~> 3.3'
    gem 'rails',           '~> 7.2'
    gem 'pg',              '~> 1.5'
    gem 'puma',            '~> 6.4'
    gem 'jwt',             '~> 2.8'
    gem 'bcrypt',          '~> 3.1'
    gem 'rack-cors'
    group :development, :test do
      gem 'rspec-rails', '~> 6.1'
    end
  GEMFILE

  # config/routes.rb
  route_resources = mods.map { |m| "    resources :#{plural(snake(m[:name]))}, only: %i[index create show update destroy]" }.join("\n")
  files['config/routes.rb'] = <<~ROUTES
    Rails.application.routes.draw do
      namespace :api do
        namespace :v1 do
          post   'auth/register', to: 'auth#register'
          post   'auth/login',    to: 'auth#login'
          delete 'auth/logout',   to: 'auth#logout'
          get    'auth/me',       to: 'auth#me'
    #{route_resources}
        end
      end
    end
  ROUTES

  # config/database.yml
  files['config/database.yml'] = <<~DB
    default: &default
      adapter: postgresql
      encoding: unicode
      pool: <%= ENV.fetch("RAILS_MAX_THREADS") { 5 } %>
      url: <%= ENV["DATABASE_URL"] %>
    development:
      <<: *default
    production:
      <<: *default
  DB

  # config/application.rb
  files['config/application.rb'] = <<~APP
    require_relative 'boot'
    require 'rails/all'
    Bundler.require(*Rails.groups)
    module #{pascal(slug.gsub('-','_'))}
      class Application < Rails::Application
        config.load_defaults 7.2
        config.api_only = true
        config.middleware.insert_before 0, Rack::Cors do
          allow { origins '*'; resource '*', headers: :any, methods: :any, credentials: true }
        end
      end
    end
  APP

  files['config/boot.rb']        = "ENV['BUNDLE_GEMFILE'] ||= File.expand_path('../Gemfile', __dir__)\nrequire 'bundler/setup'\n"
  files['config/environment.rb'] = "require_relative 'application'\nRails.application.initialize!\n"
  files['config.ru']             = "require_relative 'config/environment'\nrun Rails.application\nRails.application.load_server\n"
  files['Rakefile']              = "require_relative 'config/application'\nRails.application.load_tasks\n"

  # app/models/application_record.rb
  files['app/models/application_record.rb'] = "class ApplicationRecord < ActiveRecord::Base\n  primary_abstract_class\nend\n"

  # app/models/user.rb
  files['app/models/user.rb'] = <<~USER
    class User < ApplicationRecord
      has_secure_password
      validates :email, presence: true, uniqueness: true
      validates :name,  presence: true
    end
  USER

  # app/controllers/application_controller.rb
  files['app/controllers/application_controller.rb'] = <<~CTRL
    class ApplicationController < ActionController::API
      before_action :authenticate!

      private

      def authenticate!
        token = cookies[:access_token]
        return render json: { error: 'unauthenticated', message: 'No auth cookie.' }, status: :unauthorized unless token
        payload = JwtService.decode(token)
        return render json: { error: 'unauthenticated', message: 'Invalid or expired token.' }, status: :unauthorized unless payload
        @current_user_id = payload['sub']
      end

      def current_user_id = @current_user_id
    end
  CTRL

  # app/services/jwt_service.rb
  files['app/services/jwt_service.rb'] = <<~JWT
    require 'jwt'
    module JwtService
      TTL = ENV.fetch('JWT_TTL_SEC', 604800).to_i
      SECRET = ENV.fetch('JWT_SECRET', 'change-me')

      def self.encode(payload)
        JWT.encode(payload.merge(exp: Time.now.to_i + TTL), SECRET, 'HS256')
      end

      def self.decode(token)
        JWT.decode(token, SECRET, true, algorithm: 'HS256').first
      rescue JWT::DecodeError
        nil
      end
    end
  JWT

  # app/controllers/api/v1/auth_controller.rb
  files['app/controllers/api/v1/auth_controller.rb'] = <<~AUTH
    module Api
      module V1
        class AuthController < ApplicationController
          skip_before_action :authenticate!, only: %i[register login]

          def register
            user = User.new(name: params[:name], email: params[:email], password: params[:password])
            if user.save
              set_cookie(user)
              render json: { user: user_json(user) }, status: :created
            else
              render json: { error: 'validation_error', message: user.errors.full_messages }, status: :unprocessable_entity
            end
          end

          def login
            user = User.find_by(email: params[:email])
            if user&.authenticate(params[:password])
              set_cookie(user)
              render json: { user: user_json(user) }
            else
              render json: { error: 'invalid_credentials', message: 'Wrong email or password.' }, status: :unauthorized
            end
          end

          def logout
            cookies.delete(:access_token)
            render json: { message: 'Logged out.' }
          end

          def me
            user = User.find(@current_user_id)
            render json: { user: user_json(user) }
          end

          private

          def set_cookie(user)
            token = JwtService.encode({ 'sub' => user.id, 'email' => user.email })
            cookies[:access_token] = {
              value: token, httponly: true, same_site: :lax,
              expires: JwtService::TTL.seconds.from_now
            }
          end

          def user_json(u) = u.slice(:id, :name, :email, :created_at)
        end
      end
    end
  AUTH

  # users migration
  ts = '20240101000000'
  files["db/migrate/#{ts}_create_users.rb"] = <<~MIG
    class CreateUsers < ActiveRecord::Migration[7.2]
      def change
        create_table :users do |t|
          t.string  :name,             null: false
          t.string  :email,            null: false, index: { unique: true }
          t.string  :password_digest,  null: false
          t.timestamps
        end
      end
    end
  MIG

  # per-entity
  mods.each_with_index do |mod, idx|
    en    = mod[:name]
    sn    = snake(en)
    pl_sn = plural(sn)
    pa    = pascal(en)
    tstamp = "20240101%06d" % (idx + 1)

    user_fields = mod[:fields].reject { |f| %w[id created_at updated_at].include?(f[:name]) }
    col_defs    = user_fields.map { |f|
      next "      t.references :user, null: false, foreign_key: true, index: true" if f[:name] == 'user_id'
      pg_type = case f[:type]
                when 'text','description'     then 'text'
                when 'int','integer','bigint' then 'integer'
                when 'bool','boolean'         then 'boolean'
                when 'date'                   then 'date'
                when 'decimal','float'        then 'decimal'
                else 'string'
                end
      nullable = f[:required] ? ', null: false' : ''
      "      t.#{pg_type} :#{f[:name]}#{nullable}"
    }.join("\n")

    # model
    files["app/models/#{sn}.rb"] = <<~MODEL
      class #{pa} < ApplicationRecord
        belongs_to :user
        validates :user_id, presence: true
        scope :for_user, ->(uid) { where(user_id: uid) }
      end
    MODEL

    # controller
    permit_fields = user_fields.map { |f| f[:name] }.reject { |n| n == 'user_id' }.map { |n| ":#{n}" }.join(', ')
    files["app/controllers/api/v1/#{pl_sn}_controller.rb"] = <<~CTRL
      module Api
        module V1
          class #{pascal(pl_sn)}Controller < ApplicationController
            before_action :set_item, only: %i[show update destroy]

            def index
              render json: #{pa}.for_user(current_user_id).all
            end

            def create
              item = #{pa}.new(item_params.merge(user_id: current_user_id))
              if item.save
                render json: item, status: :created
              else
                render json: { error: 'validation_error', message: item.errors.full_messages }, status: :unprocessable_entity
              end
            end

            def show   = render json: @item
            def update = @item.update!(item_params) ? render(json: @item) : render(json: { error: 'validation_error' }, status: :unprocessable_entity)
            def destroy = @item.destroy && head(:no_content)

            private

            def set_item
              @item = #{pa}.for_user(current_user_id).find(params[:id])
            rescue ActiveRecord::RecordNotFound
              render json: { error: 'not_found', message: '#{pa} not found.' }, status: :not_found
            end

            def item_params = params.permit(#{permit_fields})
          end
        end
      end
    CTRL

    # migration
    files["db/migrate/#{tstamp}_create_#{pl_sn}.rb"] = <<~MIG
      class Create#{pascal(pl_sn)} < ActiveRecord::Migration[7.2]
        def change
          create_table :#{pl_sn} do |t|
      #{col_defs}
            t.timestamps
          end
        end
      end
    MIG
  end

  # .env.example
  files['.env.example'] = <<~ENV
    DATABASE_URL=postgresql://app:changeme@db:5432/app
    JWT_SECRET=change-me-jwt-secret-minimum-32-characters
    JWT_TTL_SEC=604800
    RAILS_ENV=production
    SECRET_KEY_BASE=change-me-rails-secret-key-base
  ENV

  # Dockerfile
  files['Dockerfile'] = <<~DOCKER
    FROM ruby:3.3-alpine AS builder
    RUN apk add --no-cache build-base postgresql-dev tzdata
    WORKDIR /app
    COPY Gemfile Gemfile.lock* ./
    RUN bundle install --without development test
    COPY . .

    FROM ruby:3.3-alpine
    RUN apk add --no-cache postgresql-client tzdata
    WORKDIR /app
    COPY --from=builder /usr/local/bundle /usr/local/bundle
    COPY --from=builder /app /app
    EXPOSE 3000
    CMD ["bundle", "exec", "rails", "server", "-b", "0.0.0.0"]
  DOCKER

  # docker-compose.yml
  files['docker-compose.yml'] = <<~DC
    services:
      app:
        build: .
        ports: ["3000:3000"]
        env_file: .env
        depends_on:
          db:
            condition: service_healthy
      db:
        image: postgres:16-alpine
        environment:
          POSTGRES_DB: app
          POSTGRES_USER: app
          POSTGRES_PASSWORD: changeme
        ports: ["5432:5432"]
        volumes: [db_data:/var/lib/postgresql/data]
        healthcheck:
          test: ["CMD-SHELL", "pg_isready -U app"]
          interval: 5s
          timeout: 5s
          retries: 10
    volumes:
      db_data:
  DC

  # ARCHITECTURE.md
  arc  = "# ARCHITECTURE — #{name}\n\nGenerated by archiet-microcodegen-rails. ArchiMate 3.2 notation.\n\n"
  arc += "## ApplicationComponent\n\n| Component | Technology | Notes |\n|---|---|---|\n"
  arc += "| ApiGateway | Rails 7 Router | Routes API requests |\n"
  arc += "| AuthService | JWT (httpOnly cookie) + bcrypt | register / login / logout |\n"
  mods.each { |m| arc += "| #{pascal(m[:name])}Service | ActiveRecord | CRUD for #{m[:name]} |\n" }
  arc += "\n## DataObject\n\n| Entity | Table | Key Fields |\n|---|---|---|\n"
  arc += "| User | users | id, name, email, password_digest |\n"
  mods.each { |m| arc += "| #{m[:name]} | #{plural(snake(m[:name]))} | #{m[:fields].first(5).map { |f| f[:name] }.join(', ')} |\n" }
  arc += "\n## Auth Contract\n- JWT in **httpOnly cookie** `access_token` — never localStorage\n"
  arc += "- Per-tenant: every ActiveRecord scope chains `.for_user(current_user_id)`\n"
  files['ARCHITECTURE.md'] = arc

  # openapi.yaml
  oa  = "openapi: \"3.1.0\"\ninfo:\n  title: \"#{name} API\"\n  version: \"0.1.0\"\npaths:\n"
  oa += "  /api/v1/auth/register:\n    post: {operationId: register, tags: [auth], responses: {201: {description: Created}}}\n"
  oa += "  /api/v1/auth/login:\n    post: {operationId: login, tags: [auth], responses: {200: {description: OK}}}\n"
  oa += "  /api/v1/auth/me:\n    get: {operationId: me, tags: [auth], security: [{cookieAuth: []}], responses: {200: {description: OK}}}\n"
  mods.each do |mod|
    sp = plural(snake(mod[:name])); pa = pascal(mod[:name])
    oa += "  /api/v1/#{sp}:\n"
    oa += "    get:  {operationId: list#{pa},   tags: [#{pa}], security: [{cookieAuth: []}], responses: {200: {description: OK}}}\n"
    oa += "    post: {operationId: create#{pa}, tags: [#{pa}], security: [{cookieAuth: []}], responses: {201: {description: Created}}}\n"
    oa += "  /api/v1/#{sp}/{id}:\n"
    oa += "    get:    {operationId: get#{pa},    tags: [#{pa}], security: [{cookieAuth: []}], responses: {200: {description: OK}}}\n"
    oa += "    put:    {operationId: update#{pa}, tags: [#{pa}], security: [{cookieAuth: []}], responses: {200: {description: OK}}}\n"
    oa += "    delete: {operationId: delete#{pa}, tags: [#{pa}], security: [{cookieAuth: []}], responses: {204: {description: No Content}}}\n"
  end
  oa += "components:\n  securitySchemes:\n    cookieAuth: {type: apiKey, in: cookie, name: access_token}\n"
  files['openapi.yaml'] = oa

  files
end

# ─── CLI ─────────────────────────────────────────────────────────────────────
def main
  options = { out: './output', zip: nil }
  OptionParser.new do |o|
    o.banner = "Usage: archiet-microcodegen-rails <prd.md> [options]"
    o.on('--out DIR',  'Write files to DIR (default: ./output)') { |v| options[:out] = v }
    o.on('--zip FILE', 'Write ZIP to FILE') { |v| options[:zip] = v }
  end.parse!

  prd_path = ARGV.shift
  if prd_path.nil? || !File.exist?(prd_path)
    warn "Error: PRD file not found: #{prd_path || '(none)'}"; exit 1
  end

  manifest = parse_prd(File.read(prd_path))
  genome   = manifest_to_genome(manifest)
  files    = render_genome(genome)

  if options[:zip]
    File.binwrite(options[:zip], pack_zip(files))
    puts "ZIP: #{options[:zip]} (#{files.size} files)"
  else
    write_disk(files, options[:out])
    puts "Done. cd #{options[:out]} && cp .env.example .env && docker compose up"
  end
end

main if $PROGRAM_NAME == __FILE__
