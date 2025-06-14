/*
name=get_user_by_id
*/
SELECT * FROM users WHERE id = :user_id;

/*
name=search_users_by_email_pattern
*/
SELECT * FROM users WHERE email LIKE :email_pattern;

/*
name=get_users_count
query_type=single
*/
SELECT COUNT(*) as total_count FROM users WHERE created_at > :since_date;

/*
name=get_single_user_by_email
query_type=single
*/
SELECT id, email FROM users WHERE email = :email LIMIT 1;
