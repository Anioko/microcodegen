Gem::Specification.new do |s|
  s.name        = 'archiet-microcodegen-rails'
  s.version     = '0.1.0'
  s.summary     = 'PRD text -> Rails 7 API app -> ZIP. Pure Ruby stdlib. <1400 LOC.'
  s.description = 'Generate a production-ready Rails 7 REST API from a requirements document. No LLM. No API key.'
  s.authors     = ['Archiet']
  s.email       = ['hello@archiet.com']
  s.homepage    = 'https://archiet.com?utm_source=rubygems&utm_medium=package&utm_campaign=microcodegen-rails'
  s.license     = 'MIT'
  s.files       = Dir['lib/**/*.rb', 'bin/*']
  s.executables = ['archiet-microcodegen-rails']
  s.required_ruby_version = '>= 3.0'
end
